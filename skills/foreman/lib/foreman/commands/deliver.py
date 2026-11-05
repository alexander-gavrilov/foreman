"""foreman deliver: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "deliver"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "deliver queued messages to an idle session")


def run(args):
    return _stub.run(NAME)
