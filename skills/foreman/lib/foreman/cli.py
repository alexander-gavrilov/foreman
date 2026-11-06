"""Argument dispatcher. Each command lives in foreman/commands/<name>.py exposing
register(subparsers) -> None (must add a parser named exactly <name>) and run(args) -> int."""
import argparse
import importlib
import sys

from foreman import __version__
from foreman.errors import ForemanError

COMMANDS = ["status", "limits", "send", "ask", "reply", "say", "open", "close", "rotate", "board", "log",
            "stage", "watch", "clean", "work", "report", "init", "install", "uninstall", "web", "serve",
            "hook", "tap", "deliver", "gate", "telegram", "orchestrator", "compact", "outbox"]


def _load(name):
    return importlib.import_module("foreman.commands." + name)


def build_parser(only=None):
    parser = argparse.ArgumentParser(prog="foreman", description="Orchestrate AI agent sessions.")
    parser.add_argument("--version", action="version", version="foreman " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    for name in ([only] if only else COMMANDS):
        try:
            mod = _load(name)
            mod.register(sub)
            sub.choices[name].set_defaults(_run=mod.run)
        except Exception as exc:  # one broken command module must not take the others down
            p = sub.add_parser(name, help="(failed to load: %s)" % exc)
            p.set_defaults(_run=_broken(name, exc))
    return parser


def _broken(name, exc):
    def run(args):
        sys.stderr.write("foreman %s: failed to load: %s\n" % (name, exc))
        return 1
    return run


def _utf8():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _kick_outbox():
    """D60: a command that queued a Telegram row hands it to a detached drainer (unless a daemon is on it)."""
    try:
        from foreman import outbox
        outbox.kick()
    except Exception:
        pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _utf8()
    try:
        # Fast path: parse with only the requested command registered (hooks run on every tool call).
        if argv and argv[0] in COMMANDS:
            try:
                mod = _load(argv[0])
            except Exception:
                mod = None
            if mod is not None and getattr(mod, "STUB", False):
                # stubs take any arguments verbatim
                return mod.run(argparse.Namespace(command=argv[0], args=argv[1:]))
            parser = build_parser(only=argv[0])
        else:
            parser = build_parser()
        args = parser.parse_args(argv)
        if not getattr(args, "_run", None):
            parser.print_help()
            return 2
        code = args._run(args)
        sys.stdout.flush()
        _kick_outbox()
        return int(code or 0)
    except ForemanError as err:
        sys.stderr.write("foreman: %s\n" % err.message)
        return err.code
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
