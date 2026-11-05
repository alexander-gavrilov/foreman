"""foreman limits: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "limits"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "subscription windows with usage, reset time and reading age")


def run(args):
    return _stub.run(NAME)
