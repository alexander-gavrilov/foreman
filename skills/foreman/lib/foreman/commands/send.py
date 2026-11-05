"""foreman send: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "send"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "message a session (queued if it is busy)")


def run(args):
    return _stub.run(NAME)
