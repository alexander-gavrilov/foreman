"""foreman hook --agent <claude|antigravity|codex> --event <native event>: the agents' hook entry point.

The dispatcher hands a STUB module its raw arguments (see cli.main), which is what a hook needs: whatever
the arguments or stdin look like, the answer is valid JSON on stdout and exit code 0 (an agent treats
a non-zero exit as a block, and agy requires JSON)."""
import argparse
import json
import sys

STUB = True  # raw-argument mode of the dispatcher; this module is not a stub

NAME = "hook"


def _parser():
    p = argparse.ArgumentParser(prog="foreman hook", add_help=False)
    p.add_argument("--agent")
    p.add_argument("--event")
    return p


def register(subparsers):
    p = subparsers.add_parser(NAME, help="receive an agent hook event on stdin (prints JSON, exits 0)",
                              add_help=False)
    p.add_argument("--agent", choices=("claude", "antigravity", "codex"))
    p.add_argument("--event", help="the agent's native hook event name")


def _stdin_text():
    try:
        return "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
    except Exception:
        return ""


def run(args):
    out = {}
    try:
        known, _ = _parser().parse_known_args(list(getattr(args, "args", None) or []))
        if known.agent and known.event:
            from foreman import hooks
            out = hooks.run_hook(known.agent, known.event, _stdin_text())
    except BaseException:  # SystemExit from argparse included: a hook must always answer
        out = {}
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return 0
