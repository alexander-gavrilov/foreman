"""foreman ask <sid|title> <text> [--timeout s]: send a question and wait for `foreman reply` (spec 4.7)."""
from foreman import messaging
from foreman.cliutil import CliError, add_project_arg, read_text_arg

DEFAULT_TIMEOUT = 900.0


def register(subparsers):
    p = subparsers.add_parser("ask", help="ask a session a question and wait for its answer")
    add_project_arg(p)
    p.add_argument("target", help="session id or title")
    p.add_argument("text", help="the question ('-' reads stdin)")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, metavar="SECONDS",
                   help="seconds to wait for the answer (default %d)" % DEFAULT_TIMEOUT)


def run(args):
    rec = messaging.find_target(args.target, args.project)
    outcome, answer = messaging.ask(rec, read_text_arg(args.text), timeout=args.timeout)
    if outcome == "terminal_gone":
        raise CliError("terminal gone: %s (%s)" % (rec.get("title") or rec["sid"], rec["sid"]), code=3)
    if outcome == "timeout":
        raise CliError("no reply from %s within %gs (the question stays queued)" % (
            rec.get("title") or rec["sid"], args.timeout), code=4)
    print(answer)
    return 0
