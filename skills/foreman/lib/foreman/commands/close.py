"""foreman close: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "close"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "close a session")


def run(args):
    return _stub.run(NAME)
