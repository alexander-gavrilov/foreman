"""foreman report: owned by another work package; this stub is replaced wholesale."""
from foreman.commands import _stub

NAME = "report"
STUB = True


def register(subparsers):
    _stub.register(subparsers, NAME, "record a finding")


def run(args):
    return _stub.run(NAME)
