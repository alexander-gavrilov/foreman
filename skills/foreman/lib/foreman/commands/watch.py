"""foreman watch: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "watch"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "run the event check once / list active watches")


def run(args):
    return _stub.run(NAME)
