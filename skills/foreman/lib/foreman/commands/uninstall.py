"""foreman uninstall: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "uninstall"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "revert `foreman install`")


def run(args):
    return _stub.run(NAME)
