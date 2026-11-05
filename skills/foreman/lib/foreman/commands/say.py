"""foreman say <text>: orchestrator -> user, into the project chat (spec 5.2, 5.3)."""
from foreman import messaging
from foreman.cliutil import CliError, add_project_arg, read_text_arg


def register(subparsers):
    p = subparsers.add_parser("say", help="tell the user something (project chat; notifies for questions)")
    add_project_arg(p)
    p.add_argument("text", help="the message ('-' reads stdin)")
    p.add_argument("--question", action="store_true", help="the message needs an answer")
    p.add_argument("--options", metavar="A|B|C", help='answer buttons, e.g. "Да|Нет|Ответить…"')
    p.add_argument("--notify", action="store_true", help="send a notification even if it is not a question")


def run(args):
    caller = messaging.current_session()
    if caller is not None and caller.get("role") == "worker":
        raise CliError("workers cannot use `foreman say` (it posts to the user's chat as the orchestrator); "
                       "report to your orchestrator with `foreman send` instead", code=2)
    slug = messaging.resolve_project_slug(args.project)
    options = [o.strip() for o in args.options.split("|") if o.strip()] if args.options else []
    msg = messaging.say(slug, read_text_arg(args.text), question=args.question, options=options,
                        notify_user=args.notify)
    print(msg["id"])
    return 0
