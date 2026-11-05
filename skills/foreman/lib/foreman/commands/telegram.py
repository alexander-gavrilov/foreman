"""foreman telegram: Bot API configuration, topic management and polling (spec §5.3, architecture §10)."""
import sys

from foreman import projects, telegram

NAME = "telegram"


def register(subparsers):
    p = subparsers.add_parser("telegram", help="Telegram Bot API management, polling and topic setup")
    sub = p.add_subparsers(dest="subcommand", metavar="<subcommand>")

    # check
    sub.add_parser("check", help="verify bot token, chat, and manage_topics permission")

    # topic <project>
    p_topic = sub.add_parser("topic", help="create a topic for a project if missing (idempotent)")
    p_topic.add_argument("project", help="project slug")

    # poll [--once] [--timeout N]
    p_poll = sub.add_parser("poll", help="poll Telegram for incoming messages and callback queries")
    p_poll.add_argument("--once", action="store_true", help="run a single poll pass and exit")
    p_poll.add_argument("--timeout", type=int, default=30, help="polling timeout in seconds (default 30)")

    # set-token
    sub.add_parser("set-token", help="read bot token from stdin and store in secrets.json (mode 0600)")

    # set-chat <id>
    p_chat = sub.add_parser("set-chat", help="set supergroup chat id in config.json")
    p_chat.add_argument("chat_id", help="supergroup chat ID (e.g. -1001234567890)")

    # allow-user <id>
    p_allow = sub.add_parser("allow-user", help="add a Telegram user ID to allowed_user_ids")
    p_allow.add_argument("user_id", help="Telegram user numerical ID")


def run(args):
    subcmd = getattr(args, "subcommand", None)

    if subcmd == "check":
        res = telegram.check()
        if not res.get("ok"):
            for m in res.get("missing", []):
                sys.stderr.write(f"missing: {m}\n")
            return 1
        bot = res.get("bot", {})
        chat = res.get("chat", {})
        print(f"ok: bot @{bot.get('username')} in chat {chat.get('title')} ({chat.get('id')})")
        return 0

    if subcmd == "topic":
        proj = projects.load_project(args.project)
        if not proj:
            sys.stderr.write(f"foreman telegram topic: unknown project '{args.project}'\n")
            return 1
        tid = telegram.create_project_topic(args.project)
        if tid is None:
            sys.stderr.write(f"foreman telegram topic: failed to create topic for '{args.project}'\n")
            return 1
        print(f"topic: {tid}")
        return 0

    if subcmd == "poll":
        if args.once:
            count = telegram.poll_once(timeout=0)
            print(f"polled {count} update(s)")
            return 0
        while True:
            telegram.poll_once(timeout=args.timeout)

    if subcmd == "set-token":
        token = sys.stdin.read().strip()
        if not token:
            sys.stderr.write("foreman telegram set-token: empty token\n")
            return 1
        telegram.set_token(token)
        print("telegram token saved")
        return 0

    if subcmd == "set-chat":
        cid = telegram.set_chat(args.chat_id)
        print(f"telegram chat set to {cid}")
        return 0

    if subcmd == "allow-user":
        uid = telegram.allow_user(args.user_id)
        print(f"allowed telegram user {uid}")
        return 0

    sys.stderr.write("foreman telegram: missing subcommand (check, topic, poll, set-token, set-chat, allow-user)\n")
    return 2
