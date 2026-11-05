"""Shared body for commands that other work packages implement later. A stub module sets STUB = True,
so the dispatcher hands it the raw arguments; replacing a stub means replacing its file entirely."""
import sys


def register(subparsers, name, help_text):
    subparsers.add_parser(name, help=help_text + " (not implemented yet)", add_help=False)


def run(name):
    sys.stderr.write("foreman %s: not implemented yet\n" % name)
    return 2
