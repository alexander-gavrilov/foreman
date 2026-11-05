"""foreman init: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "init"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "set up a project (safe to run again)")


def run(args):
    return _stub.run(NAME)
