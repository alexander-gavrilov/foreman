"""foreman serve: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "serve"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "run the daemon: web UI, Telegram poller, watch loop")


def run(args):
    return _stub.run(NAME)
