"""Projects, board, journal and chat (architecture §2). A project is keyed by a slug derived from its folder."""
import os
import re

from foreman import ids, paths, store, timeutil

BOARD_FIELDS = ("title", "stage", "owner", "next", "status", "check")
SETTABLE_STATUSES = ("open", "blocked", "waiting")


# ---------------------------------------------------------------- projects

def slugify(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "project"


def _normalize(path):
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def load_project(slug):
    """The project record, None if it does not exist; StateCorrupt (file moved aside) if it is unreadable."""
    path = paths.project_file(slug)
    data = store.read_json(path)
    if data is None:
        return None
    if not isinstance(data, dict):
        store.quarantine(path, "not a JSON object")
    return data


def save_project(project):
    store.write_json(paths.project_file(project["slug"]), project)
    return project


def list_projects():
    root = paths.projects_dir()
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        if name.startswith(".") or name.endswith(".lock"):
            continue
        try:
            proj = load_project(name)
        except (store.StateCorrupt, ValueError):
            continue  # already moved aside (or not a valid slug); keep listing the healthy ones
        if proj and proj.get("slug"):
            out.append(proj)
    return out


def create_project(path, name=None, language=None):
    """Register `path` as a project (idempotent: an already-registered folder returns its record).
    The path check and slug allocation happen under one lock on projects/, and project.json is created
    exclusively, so concurrent callers never get two projects for one path or two paths for one slug."""
    folder = _normalize(path)
    base = slugify(os.path.basename(folder.rstrip(os.sep)) or "project")
    registry = os.path.join(paths.projects_dir(), ".registry")
    with store.locked(registry):
        for existing in list_projects():
            if existing.get("path") == folder:
                return existing
        n = 1
        while True:
            slug = base if n == 1 else "%s-%d" % (base, n)
            n += 1
            project = {
                "slug": slug,
                "name": name or os.path.basename(folder.rstrip(os.sep)) or slug,
                "path": folder,
                "created_at": timeutil.now_iso(),
                "orchestrator": None,
                "telegram_topic_id": None,
                "gates": [],
                "language": language,
            }
            try:
                store.write_json_exclusive(paths.project_file(slug), project)
            except FileExistsError:
                continue
            break
        if not os.path.exists(paths.board_file(slug)):
            save_board(slug, {"tasks": [], "backlog": []})
        return project


def project_for_path(path):
    """The initialised project whose folder (or one of whose worker worktrees) contains `path`, deepest wins, else None."""
    target = _normalize(path)
    wt_root = _normalize(paths.worktrees_root()) + os.sep  # a worker's worktree belongs to its project
    if target.startswith(wt_root):
        slug = target[len(wt_root):].split(os.sep)[0]
        if slug and load_project(slug):
            return load_project(slug)
    best = None
    for proj in list_projects():
        root = proj.get("path")
        if not root:
            continue
        if target == root or target.startswith(root.rstrip(os.sep) + os.sep):
            if best is None or len(root) > len(best["path"]):
                best = proj
    return best


# ---------------------------------------------------------------- board

def _check_board(path, board):
    """A board must be an object whose tasks/backlog are lists; anything else is corrupt state."""
    if not isinstance(board, dict) or not isinstance(board.get("tasks", []), list) \
            or not isinstance(board.get("backlog", []), list):
        store.quarantine(path, "not a board object")
    board.setdefault("tasks", [])
    board.setdefault("backlog", [])
    return board


def load_board(slug):
    """The board; an empty one if the file is missing; StateCorrupt (file moved aside) if it is unreadable."""
    path = paths.board_file(slug)
    return _check_board(path, store.read_json(path, {}))


def save_board(slug, board):
    store.write_json(paths.board_file(slug), board)
    return board


def list_tasks(slug):
    return load_board(slug)["tasks"]


def get_task(slug, task_id):
    for t in list_tasks(slug):
        if t["id"] == task_id:
            return t
    return None


def _next_task_id(board):
    highest = board.get("last_id", 0)
    for t in board["tasks"]:
        m = re.match(r"^t(\d+)$", t.get("id", ""))
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _mutate_board(slug, fn):
    """Locked read-modify-write of board.json; fn(board) -> result."""
    result = {}

    path = paths.board_file(slug)

    def apply(board):
        board = _check_board(path, board)
        result["value"] = fn(board)
        return board

    store.update_json(path, apply, default={})
    return result["value"]


def append_backlog(slug, summary):
    def fn(board):
        board["backlog"].append(summary)
        return board
    return _mutate_board(slug, fn)


def add_task(slug, title, stage=None, owner=None, next=None, links=None):
    def fn(board):
        n = _next_task_id(board)
        board["last_id"] = n  # ids are never reused, even after a task is removed
        task = {"id": "t%d" % n, "title": title, "stage": stage, "owner": owner, "next": next,
                "links": list(links or []), "status": "open", "check": None,
                "updated_at": timeutil.now_iso()}
        board["tasks"].append(task)
        return task
    return _mutate_board(slug, fn)


def _find(board, task_id):
    for t in board["tasks"]:
        if t["id"] == task_id:
            return t
    raise KeyError(task_id)


def set_task_fields(slug, task_id, fields):
    """Set title/stage/owner/next/status/check(cmd) or append a link ('link' = 'label=url')."""
    for key, value in fields.items():
        if key not in BOARD_FIELDS and key != "link":
            raise ValueError("unknown field %r (allowed: %s, link)" % (key, ", ".join(BOARD_FIELDS)))
        if key == "status" and value not in SETTABLE_STATUSES:
            raise ValueError("status %r cannot be set directly; use `foreman board done` (allowed: %s)"
                             % (value, ", ".join(SETTABLE_STATUSES)))
        if key == "link" and "=" not in value:
            raise ValueError("link must be label=url")
        if key == "check" and not value:
            raise ValueError("check must be a non-empty shell command")

    def fn(board):
        task = _find(board, task_id)
        if "check" in fields and (task.get("check") or {}).get("cmd"):
            raise ValueError("task %s already has a check (%s); an objective check cannot be replaced"
                             % (task_id, task["check"]["cmd"]))
        for key, value in fields.items():
            if key == "link":
                label, url = value.split("=", 1)
                task["links"].append({"label": label, "url": url})
            elif key == "check":
                task["check"] = {"cmd": value, "exit_code": None, "tail": None, "ran_at": None}
            else:
                task[key] = value
        task["updated_at"] = timeutil.now_iso()
        return task
    return _mutate_board(slug, fn)


def record_check(slug, task_id, check):
    """Store a check result ({cmd, exit_code, tail}) without changing the task status."""
    def fn(board):
        task = _find(board, task_id)
        task["check"] = dict(check, ran_at=timeutil.now_iso())
        task["updated_at"] = timeutil.now_iso()
        return task
    return _mutate_board(slug, fn)


def mark_done(slug, task_id, check=None):
    def fn(board):
        task = _find(board, task_id)
        if check is not None:
            task["check"] = dict(check, ran_at=timeutil.now_iso())
        task["status"] = "done"
        task["updated_at"] = timeutil.now_iso()
        return task
    return _mutate_board(slug, fn)


# ---------------------------------------------------------------- journal

def journal_append(slug, actor, kind, text, data=None):
    entry = {"ts": timeutil.now_iso(), "actor": actor, "kind": kind, "text": text, "data": data}
    return store.append_jsonl(paths.journal_file(slug), entry)


def journal_search(slug, pattern, limit=None):
    return store.search_jsonl(paths.journal_file(slug), pattern, field="text", limit=limit)


def journal_tail(slug, n):
    return store.tail_jsonl(paths.journal_file(slug), n)


# ---------------------------------------------------------------- chat

def chat_append(slug, sender, text, channel="cli", question=False, options=None, reply_to=None):
    msg = {"id": ids.new_msg_id(), "ts": timeutil.now_iso(), "from": sender, "channel": channel,
           "text": text, "question": bool(question), "options": list(options or []),
           "reply_to": reply_to, "answered_by": None}
    return store.append_jsonl(paths.chat_file(slug), msg)


def chat_list(slug, limit=None):
    rows = store.read_jsonl(paths.chat_file(slug))
    return rows[-limit:] if limit else rows
