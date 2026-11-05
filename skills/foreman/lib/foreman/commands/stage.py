"""foreman stage: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "stage"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "report a stage change of a task")


def run(args):
    return _stub.run(NAME)
