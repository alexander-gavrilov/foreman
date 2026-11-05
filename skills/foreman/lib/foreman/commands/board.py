"""foreman board: show / add / set / done (spec §4.8)."""
import os
import subprocess
import sys

from foreman import projects
from foreman.cliutil import CliError, add_project_arg, dump_json, resolve_project

TAIL_LINES = 20
TAIL_CHARS = 4000
CHECK_TIMEOUT_S = 1800


def register(subparsers):
    p = subparsers.add_parser("board", help="the project board: show, add, set, done")
    sub = p.add_subparsers(dest="board_cmd", metavar="<action>", required=True)

    s = sub.add_parser("show", help="print the board")
    add_project_arg(s)
    s.add_argument("--json", action="store_true")

    a = sub.add_parser("add", help="add a task; prints its id")
    add_project_arg(a)
    a.add_argument("title")
    a.add_argument("--stage")
    a.add_argument("--owner")
    a.add_argument("--next", dest="next_step")
    a.add_argument("--link", action="append", default=[], metavar="LABEL=URL")

    t = sub.add_parser("set", help="set task fields: stage= owner= next= title= status= check= link=label=url")
    add_project_arg(t)
    t.add_argument("task_id")
    t.add_argument("fields", nargs="+", metavar="field=value")

    d = sub.add_parser("done", help="mark a task done, only if its check passes")
    add_project_arg(d)
    d.add_argument("task_id")
    d.add_argument("--check", metavar="CMD",
                   help="shell command that must exit 0; only used when the task has no stored check "
                        "(a stored check is fixed and cannot be replaced)")


def run(args):
    proj = resolve_project(args)
    return {"show": _show, "add": _add, "set": _set, "done": _done}[args.board_cmd](args, proj)


def _task_line(t):
    parts = ["%s [%s] %s" % (t["id"], "x" if t.get("status") == "done" else " ", t["title"])]
    if t.get("stage"):
        parts[0] += " (%s)" % t["stage"]
    if t.get("status") not in (None, "open", "done"):
        parts.append(t["status"])
    if t.get("owner"):
        parts.append("owner %s" % t["owner"])
    if t.get("next"):
        parts.append("next: %s" % t["next"])
    chk = t.get("check") or {}
    if chk.get("exit_code") not in (None, 0):
        parts.append("check failed (%s)" % chk["exit_code"])
    for link in t.get("links") or []:
        parts.append("%s=%s" % (link["label"], link["url"]))
    return " · ".join(parts)


def _show(args, proj):
    board = projects.load_board(proj["slug"])
    if args.json:
        dump_json({"project": proj["slug"], "tasks": board["tasks"], "backlog": board["backlog"]})
        return 0
    tasks = board["tasks"]
    print("# %s — %s" % (proj["slug"], ("%d task%s" % (len(tasks), "" if len(tasks) == 1 else "s"))
                         if tasks else "no tasks"))
    for t in tasks:
        print(_task_line(t))
    return 0


def _parse_link(text):
    if "=" not in text or text.startswith("="):
        raise CliError("--link must be label=url, got %r" % text)
    label, url = text.split("=", 1)
    return {"label": label, "url": url}


def _add(args, proj):
    links = [_parse_link(x) for x in args.link]
    task = projects.add_task(proj["slug"], args.title, stage=args.stage, owner=args.owner,
                             next=args.next_step, links=links)
    print(task["id"])
    return 0


def _set(args, proj):
    fields = {}
    for item in args.fields:
        if "=" not in item or item.startswith("="):
            raise CliError("expected field=value, got %r" % item)
        key, value = item.split("=", 1)
        if key == "link":
            _parse_link(value)
        fields[key] = value
    try:
        projects.set_task_fields(proj["slug"], args.task_id, fields)
    except KeyError:
        raise CliError("no task %s in project %s" % (args.task_id, proj["slug"]))
    except ValueError as exc:
        raise CliError(str(exc))
    return 0


def _run_check(cmd, cwd):
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=CHECK_TIMEOUT_S)
        code, raw = proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as exc:
        code, raw = 124, (exc.stdout or b"") + b"\n[timed out after %ds]" % CHECK_TIMEOUT_S
    text = raw.decode("utf-8", "replace")
    tail = "\n".join(text.rstrip("\n").splitlines()[-TAIL_LINES:])[-TAIL_CHARS:]
    return code, tail


def _done(args, proj):
    slug = proj["slug"]
    task = projects.get_task(slug, args.task_id)
    if task is None:
        raise CliError("no task %s in project %s" % (args.task_id, slug))
    stored = (task.get("check") or {}).get("cmd")
    if stored:
        # The objective check is fixed once set: --check can never replace it.
        cmd = stored
        if args.check and args.check != stored:
            sys.stderr.write("foreman: --check ignored: %s already has a stored check (%s)\n"
                             % (args.task_id, stored))
    else:
        cmd = args.check
    if not cmd:
        projects.mark_done(slug, args.task_id)
        print("%s done" % args.task_id)
        return 0
    cwd = proj.get("path") if os.path.isdir(proj.get("path") or "") else None
    if cwd is None:
        raise CliError("project folder %s does not exist; cannot run the check" % proj.get("path"))
    code, tail = _run_check(cmd, cwd)
    result = {"cmd": cmd, "exit_code": code, "tail": tail}
    if code == 0:
        projects.mark_done(slug, args.task_id, check=result)
        print("%s done (check passed)" % args.task_id)
        return 0
    projects.record_check(slug, args.task_id, result)
    if tail:
        print(tail)
    sys.stderr.write("foreman: check failed (exit %d); %s stays open\n" % (code, args.task_id))
    return 1
