"""foreman reply: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "reply"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "answer a foreman ask / question")


def run(args):
    return _stub.run(NAME)
