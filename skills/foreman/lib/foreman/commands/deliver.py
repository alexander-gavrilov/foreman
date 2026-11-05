"""foreman deliver <sid>: deliver the session's queued messages now if its terminal is idle."""
from foreman import delivery, messaging


def register(subparsers):
    p = subparsers.add_parser("deliver", help="deliver queued messages to an idle session")
    p.add_argument("sid", help="session id or title")
    p.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                   help="first wait up to this long for the terminal to become idle")


def run(args):
    rec = messaging.find_target(args.sid)
    if args.wait > 0 and (rec.get("transport") or {}).get("handle"):
        try:
            from foreman.transports import get_transport
            get_transport(rec["transport"]["kind"]).wait_idle(rec["transport"]["handle"], args.wait)
        except Exception:
            pass  # a gone terminal is reported by deliver_pending below
    status = delivery.deliver_pending(rec["sid"])
    print(status)
    return 3 if status == "terminal_gone" else 0
