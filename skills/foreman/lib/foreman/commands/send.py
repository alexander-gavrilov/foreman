"""foreman send <sid|title> <text|->: message a session; queued if it is busy (spec 4.7)."""
from foreman import messaging
from foreman.cliutil import CliError, add_project_arg, read_text_arg


def register(subparsers):
    p = subparsers.add_parser("send", help="message a session (queued if it is busy)")
    add_project_arg(p)
    p.add_argument("target", help="session id or title")
    p.add_argument("text", help="the message ('-' reads stdin)")


def run(args):
    rec = messaging.find_target(args.target, args.project)
    status, _ = messaging.send_message(rec, read_text_arg(args.text))
    if status == "terminal_gone":
        raise CliError("terminal gone: %s (%s)" % (rec.get("title") or rec["sid"], rec["sid"]), code=3)
    print("%s %s" % (status, rec["sid"]))
    return 0
