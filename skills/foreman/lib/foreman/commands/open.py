"""foreman open: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "open"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "open a new agent session")


def run(args):
    return _stub.run(NAME)
