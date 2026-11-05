"""foreman init: set up a project (safe to run again)."""
from foreman import initproj
from foreman.cliutil import CliError

NAME = "init"


def register(subparsers):
    p = subparsers.add_parser(NAME, help="set up a project: state, board, hooks, work-file rule (safe to run again)")
    p.add_argument("--path", default=".", help="project folder (default: the current folder)")
    p.add_argument("--name", help="project name (default: the folder name)")


def run(args, **kw):
    try:
        changes, notes = initproj.init_project(args.path, name=args.name, **kw)
    except initproj.InitError as exc:
        raise CliError(str(exc), 1)
    for line in changes:
        print(line)
    if not changes:
        print("already correct")
    for line in notes:
        print("note: " + line)
    return 0
