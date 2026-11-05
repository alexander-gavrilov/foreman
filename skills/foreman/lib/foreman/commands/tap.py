"""foreman tap: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "tap"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "statusline tap: record limits and context, pass the output through")


def run(args):
    return _stub.run(NAME)
