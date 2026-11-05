"""foreman work: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "work"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "create or close a work-files folder")


def run(args):
    return _stub.run(NAME)
