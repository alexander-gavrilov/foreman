"""foreman outbox drain: the detached, bounded drainer a CLI process starts when it queued a Telegram row and no
daemon is running (D60). Not meant to be typed by hand."""
import argparse
import os

from foreman import outbox

NAME = "outbox"


def register(subparsers):
    p = subparsers.add_parser("outbox", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="outbox_cmd", metavar="<subcommand>")
    d = sub.add_parser("drain", help=argparse.SUPPRESS)
    d.add_argument("--budget", type=float, default=None, help="seconds to work at most")


def run(args):
    if getattr(args, "outbox_cmd", None) != "drain":
        return 2
    budget = args.budget
    if budget is None:
        budget = float(os.environ.get("FOREMAN_OUTBOX_BUDGET_S") or outbox.DETACHED_BUDGET_S)
    outbox.drain_detached(budget_s=budget)
    return 0
