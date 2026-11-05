"""foreman ask <sid|title> <text> [--wait S]: send a question; the answer arrives in your inbox (spec 4.7)."""
from foreman import messaging
from foreman.cliutil import CliError, add_project_arg, read_text_arg


def register(subparsers):
    p = subparsers.add_parser("ask", help="ask a session a question (asynchronous; the answer is queued for you)")
    add_project_arg(p)
    p.add_argument("target", help="session id or title")
    p.add_argument("text", help="the question ('-' reads stdin)")
    p.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                   help="also wait up to this long (at most %d) for a quick answer; exit 4 if none arrived"
                        % messaging.MAX_WAIT_S)


def run(args):
    if args.wait < 0 or args.wait > messaging.MAX_WAIT_S:
        raise CliError("--wait must be between 0 and %d seconds (agent tool calls time out)" % messaging.MAX_WAIT_S)
    rec = messaging.find_target(args.target, args.project)
    status, msg_id = messaging.ask(rec, read_text_arg(args.text))
    if status == "terminal_gone":
        raise CliError("terminal gone: %s (%s)" % (rec.get("title") or rec["sid"], rec["sid"]), code=3)
    print(msg_id)
    if args.wait > 0:
        answer = messaging.wait_for_reply(msg_id, args.wait)
        if answer is None:
            raise CliError("no reply from %s within %gs; the answer will still arrive in your inbox" % (
                rec.get("title") or rec["sid"], args.wait), code=4)
        print(answer)
    return 0
