"""foreman telegram: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "telegram"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "Telegram setup and tools")


def run(args):
    return _stub.run(NAME)
