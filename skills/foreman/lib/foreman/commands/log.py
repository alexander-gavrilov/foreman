"""foreman log: append to / search the project journal (spec §4.8)."""
import os
import sys

from foreman import projects
from foreman.cliutil import add_project_arg, dump_json, read_text_arg, resolve_project

DEFAULT_LIMIT = 20


def register(subparsers):
    p = subparsers.add_parser("log", help="the project journal: append, search")
    sub = p.add_subparsers(dest="log_cmd", metavar="<action>", required=True)

    a = sub.add_parser("append", help="append one journal line")
    add_project_arg(a)
    a.add_argument("text", help="the line ('-' reads stdin)")
    a.add_argument("--kind", default="note")
    a.add_argument("--actor", default=None, help="default: $FOREMAN_ACTOR, else 'cli'")

    s = sub.add_parser("search", help="search journal text (case-insensitive regex or substring)")
    add_project_arg(s)
    s.add_argument("pattern")
    s.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="newest N matches (default %d)" % DEFAULT_LIMIT)
    s.add_argument("--json", action="store_true")


def run(args):
    proj = resolve_project(args)
    if args.log_cmd == "append":
        actor = args.actor or os.environ.get("FOREMAN_ACTOR") or "cli"
        projects.journal_append(proj["slug"], actor, args.kind, read_text_arg(args.text))
        return 0
    hits = projects.journal_search(proj["slug"], args.pattern, limit=args.limit)
    if args.json:
        dump_json(hits)
    else:
        for e in hits:
            print("%s  %s  %s  %s" % (e["ts"], e["actor"], e["kind"], e["text"]))
    return 0 if hits else 1
