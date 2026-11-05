"""foreman report: record a finding (spec 4.11). Foreman findings are scrubbed first; project findings go to
the project's lessons.md. --list shows findings; --send <id> sends a draft."""
from foreman import findings, projects
from foreman.cliutil import CliError, read_text_arg, resolve_project

NAME = "report"


def register(subparsers):
    p = subparsers.add_parser(NAME, help="record a finding (about foreman, or a project rule)")
    p.add_argument("text", nargs="?", help="the finding ('-' reads stdin)")
    p.add_argument("--kind", choices=("foreman", "project"), default=None,
                   help="foreman: about the plugin itself (default); project: a rule for this project")
    p.add_argument("--project", dest="as_project", action="store_true", help="same as --kind project")
    p.add_argument("--slug", metavar="SLUG", help="project slug for a project finding (default: from the cwd)")
    p.add_argument("--list", action="store_true", help="list the findings")
    p.add_argument("--send", metavar="ID", help="send a draft or failed finding upstream")


def run(args, runner=None):
    if args.list:
        for f in findings.list_findings():
            print("%s  %-6s  %s%s%s" % (f["id"], f["status"], f["title"], "  " + f["url"] if f.get("url") else "",
                                      "  [held: %s]" % f["reason"] if f.get("reason") else ""))
        return 0
    if args.send:
        f = findings.send(args.send, runner=runner)
        if f is None:
            raise CliError("unknown finding %r" % args.send)
        print("%s: %s%s" % (f["id"], f["status"], " " + f["url"] if f.get("url") else ""))
        return 0 if f["status"] == "sent" else 1
    if not args.text:
        raise CliError("nothing to report: foreman report <text> [--kind foreman|project]")
    text = read_text_arg(args.text)
    kind = "project" if args.as_project else (args.kind or "foreman")
    if kind == "project":
        proj = resolve_project(type("A", (), {"project": args.slug})())
        path = findings.report_project(proj["slug"], text)
        print("noted in the lessons of %s" % proj["slug"])
        return 0
    f = findings.report_foreman(text, runner=runner)
    if f["status"] == "sent":
        print("reported: %s" % f["url"])
    elif f["status"] == "draft":
        why = " - held back from auto-send: %s" % f["reason"] if f.get("reason") else ""
        print("saved as draft %s%s (send it with: foreman report --send %s)" % (f["id"], why, f["id"]))
    elif f["status"] == "failed":
        print("could not send (%s); saved as draft %s" % (f.get("error"), f["id"]))
    else:
        print("saved locally as %s (upstream reporting is off)" % f["id"])
    return 0
