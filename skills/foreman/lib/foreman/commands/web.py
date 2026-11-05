"""foreman web: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "web"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "start the web UI / pair a device")


def run(args):
    return _stub.run(NAME)
