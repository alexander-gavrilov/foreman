"""foreman hook: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "hook"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "receive an agent hook event on stdin")


def run(args):
    return _stub.run(NAME)
