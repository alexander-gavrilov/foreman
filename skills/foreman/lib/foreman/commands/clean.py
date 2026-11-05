"""foreman clean: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "clean"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "clean up old work files")


def run(args):
    return _stub.run(NAME)
