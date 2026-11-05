"""foreman install: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "install"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "install foreman on this machine")


def run(args):
    return _stub.run(NAME)
