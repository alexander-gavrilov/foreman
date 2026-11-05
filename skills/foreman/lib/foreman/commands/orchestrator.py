"""foreman orchestrator: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "orchestrator"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "start or inspect a project's orchestrator")


def run(args):
    return _stub.run(NAME)
