"""foreman rotate: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "rotate"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "freeze a session and replace it with a fresh one")


def run(args):
    return _stub.run(NAME)
