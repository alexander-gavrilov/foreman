"""foreman compact <sid|title> [what to keep]: compact a session in place at its idle point (spec 4.3)."""
from foreman import messaging
from foreman.cliutil import add_project_arg


def register(subparsers):
    p = subparsers.add_parser("compact", help="type /compact into an idle agent session")
    add_project_arg(p)
    p.add_argument("target", help="session id or title")
    p.add_argument("what", nargs="*", help="what the summary must keep")


def run(args):
    rec = messaging.find_target(args.target, args.project)
    print(messaging.compact(rec, " ".join(args.what)))
    return 0
