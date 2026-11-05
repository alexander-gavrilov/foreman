"""Project init (spec 5.4, 4.9 rule, architecture 4a/11). Idempotent: running it again changes nothing that is
already correct. Returns (changes, notes): `changes` are things this run altered, `notes` are information."""
import json
import os
import re
import shlex
import shutil

from foreman import install as machine
from foreman import projects, sessions, store, telegram

MARK_START, MARK_END = "<!-- foreman:rules -->", "<!-- /foreman:rules -->"
_BLOCK_RE = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END), re.S)

CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "Notification",
                 "PermissionRequest", "SessionEnd")
CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop",
                "SessionEnd")
TOOL_EVENTS = ("PreToolUse", "PostToolUse")
AGY_FLAT = ("SessionStart", "PreInvocation", "Stop")

# agent -> (binaries on PATH, config dirs under HOME)
AGENTS = {
    "claude": (("claude",), (".claude",)),
    "antigravity": (("agy", "antigravity"), (".gemini/antigravity-cli",)),
    "codex": (("codex",), (".codex",)),
}
HOOK_FILES = {"claude": ".claude/settings.local.json", "antigravity": ".agents/hooks.json",
              "codex": ".codex/hooks.json"}


class InitError(Exception):
    pass


def detect_agents(home=None, which=None):
    """Agents present on this machine: a binary on PATH or a config dir under HOME."""
    which = which or shutil.which
    home = machine.user_home(home)
    found = []
    for agent, (bins, dirs) in AGENTS.items():
        if any(which(b) for b in bins) or any(os.path.isdir(os.path.join(home, d)) for d in dirs):
            found.append(agent)
    return found


def _cmd(fm, agent, event):
    return "%s hook --agent %s --event %s" % (shlex.quote(fm), agent, event)


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise InitError("%s is not valid JSON; fix or remove it, then run init again" % path)
    if not isinstance(data, dict):
        raise InitError("%s does not hold a JSON object" % path)
    return data


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def merge_claude_layout(data, agent, events, fm):
    """Add/refresh foreman's handler for each event, keeping every other hook. Returns the new dict."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    for event in events:
        want = _cmd(fm, agent, event)
        tail = re.compile(r"hook --agent %s --event %s$" % (agent, event))
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = hooks[event] = []
        found = False
        for group in groups:
            for h in (group.get("hooks") if isinstance(group, dict) else None) or []:
                if isinstance(h, dict) and tail.search(h.get("command") or ""):
                    found = True
                    h["command"] = want
        if not found:
            handler = {"type": "command", "command": want}
            if event != "SessionEnd":
                handler["timeout"] = 10
            group = {"hooks": [handler]}
            if event in TOOL_EVENTS:
                group = {"matcher": "*", "hooks": [handler]}
            groups.append(group)
    return data


def antigravity_hooks(fm):
    def handler(event):
        return {"type": "command", "command": _cmd(fm, "antigravity", event), "timeout": 10}
    entry = {"enabled": True}
    for event in AGY_FLAT[:1] + ("PreInvocation",) + AGY_FLAT[2:]:
        entry[event] = [handler(event)]
    for event in TOOL_EVENTS:
        entry[event] = [{"matcher": "*", "hooks": [handler(event)]}]
    return entry


def write_hooks(project_path, agent, fm):
    """Create/merge the project hook file for `agent`. Returns True when the file changed."""
    path = os.path.join(project_path, HOOK_FILES[agent])
    data = _load_json(path)
    before = json.dumps(data, sort_keys=True)
    data = data or {}
    if agent == "antigravity":
        data["foreman"] = antigravity_hooks(fm)
    else:
        merge_claude_layout(data, agent, CLAUDE_EVENTS if agent == "claude" else CODEX_EVENTS, fm)
    if os.path.exists(path) and json.dumps(data, sort_keys=True) == before:
        return False
    _save_json(path, data)
    return True


# ------------------------------------------------------------ git exclude

def _exclude_file(project_path):
    git = os.path.join(project_path, ".git")
    if os.path.isfile(git):  # worktree / submodule: `gitdir: <path>`
        try:
            line = open(git, encoding="utf-8").read().strip()
        except OSError:
            return None
        if not line.startswith("gitdir:"):
            return None
        gitdir = line[len("gitdir:"):].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(project_path, gitdir)
        common = os.path.join(gitdir, "commondir")
        if os.path.exists(common):
            gitdir = os.path.normpath(os.path.join(gitdir, open(common, encoding="utf-8").read().strip()))
        return os.path.join(gitdir, "info", "exclude")
    if os.path.isdir(git):
        return os.path.join(git, "info", "exclude")
    return None


def exclude_from_git(project_path, rel_paths):
    """Append missing entries to .git/info/exclude (never .gitignore). Returns the entries added."""
    target = _exclude_file(project_path)
    if not target:
        return []
    try:
        text = open(target, encoding="utf-8").read()
    except OSError:
        text = ""
    have = {ln.strip() for ln in text.splitlines()}
    missing = [r for r in rel_paths if "/" + r not in have and r not in have]
    if not missing:
        return []
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        for r in missing:
            f.write("/" + r + "\n")
    return missing


# ------------------------------------------------------------ rule block

def rules_block():
    with open(os.path.join(machine.skill_dir(), "templates", "rules-block.md"), encoding="utf-8") as f:
        return f.read().strip()


def apply_rules(path, block):
    """Create the file, append the block, or replace a differing block. Returns 'created'|'added'|'updated'|None."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = None
    if text is None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(block + "\n")
        return "created"
    m = _BLOCK_RE.search(text)
    if m:
        if m.group(0) == block:
            return None
        new, how = text[:m.start()] + block + text[m.end():], "updated"
    else:
        sep = "" if not text.strip() else ("\n" if text.endswith("\n\n") else "\n\n" if text.endswith("\n") else "\n\n")
        new, how = text + sep + block + "\n", "added"
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    return how


# ------------------------------------------------------------ sessions

def available_transports():
    if os.environ.get("FOREMAN_FAKE_TRANSPORT"):
        from foreman.transports import get_transport
        return [get_transport("fake")]
    out = []
    for kind in ("orca", "herdr"):
        try:
            from foreman.transports import get_transport
            out.append(get_transport(kind))
        except Exception:
            continue
    return out


def _inside(cwd, root):
    if not cwd:
        return False
    cwd = os.path.realpath(cwd)
    return cwd == root or cwd.startswith(root.rstrip(os.sep) + os.sep)


def register_open_sessions(project, transports):
    """Sessions already open in the project's folder become owner=user workers. Returns their sids."""
    added = []
    for tr in transports:
        try:
            terms = tr.list()
        except Exception:
            continue
        for term in terms:
            if not term.alive or not _inside(term.cwd, project["path"]):
                continue
            if sessions.find_by_handle(term.handle, tr.kind):
                continue
            rec = sessions.create_session(
                project["slug"], term.agent or "unknown", role="worker", owner="user", title=term.title or None,
                transport={"kind": tr.kind, "handle": term.handle}, cwd=term.cwd)
            added.append(rec["sid"])
    return added


# ------------------------------------------------------------ init

def init_project(path, name=None, home=None, agents=None, transports=None, fm=None):
    path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isdir(path):
        raise InitError("%s is not a folder" % path)
    changes, notes = [], []
    fm = fm or machine.foreman_bin(home)

    known = any(p.get("path") == path for p in projects.list_projects())
    project = projects.create_project(path, name=name)
    if not known:
        changes.append("registered project %s" % project["slug"])
    elif name and project.get("name") != name:
        project["name"] = name
        projects.save_project(project)
        changes.append("renamed project %s to %r" % (project["slug"], name))

    agents = detect_agents(home) if agents is None else list(agents)
    written = []
    for agent in agents:
        if write_hooks(path, agent, fm):
            changes.append("wrote %s hooks (%s)" % (agent, HOOK_FILES[agent]))
            if agent == "codex":
                notes.append("Codex: approve the foreman hook once in Codex /hooks (foreman never trusts itself)")
        written.append(HOOK_FILES[agent])
    for rel in exclude_from_git(path, written):
        changes.append("excluded %s from git (.git/info/exclude)" % rel)

    block = rules_block()
    for fname in ("AGENTS.md", "CLAUDE.md"):
        how = apply_rules(os.path.join(path, fname), block)
        if how:
            changes.append("%s the work-file rule in %s" % ({"created": "added", "added": "added",
                                                           "updated": "updated"}[how], fname))

    if project.get("telegram_topic_id") is None:
        try:
            topic = telegram.create_topic(project["name"])
        except Exception as exc:
            topic, _ = None, notes.append("Telegram topic not created: %s" % exc)
        if topic is not None:
            project["telegram_topic_id"] = topic
            projects.save_project(project)
            changes.append("created the Telegram topic")
        elif not any(n.startswith("Telegram topic") for n in notes):
            notes.append("no Telegram on this machine")

    for sid in register_open_sessions(project, available_transports() if transports is None else transports):
        changes.append("registered the open session %s (owner user)" % sid)
    return changes, notes
