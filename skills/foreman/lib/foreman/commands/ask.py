"""foreman ask: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "ask"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "message a session and wait for its answer")


def run(args):
    return _stub.run(NAME)
