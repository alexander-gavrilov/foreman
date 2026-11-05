"""foreman say: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "say"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "orchestrator -> user message")


def run(args):
    return _stub.run(NAME)
