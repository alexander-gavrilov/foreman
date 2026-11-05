"""foreman tap: capture stdin, record limits/context, and pass stdin to original command."""
import argparse
import subprocess
import sys


def register(subparsers):
    parser = subparsers.add_parser("tap", help="Wrap a command to capture its stdin")
    sub = parser.add_subparsers(dest="tap_subcommand", metavar="<subcommand>")
    p_sl = sub.add_parser("statusline", help="Statusline tap")
    p_sl.add_argument("--agent", required=True, choices=["claude", "antigravity", "agy"], help="Agent kind")
    p_sl.add_argument("cmd", nargs=argparse.REMAINDER, help="Original command to execute")


def run(args):
    subcmd = getattr(args, "tap_subcommand", None)
    if not subcmd:
        sys.stderr.write("foreman tap: missing subcommand (try `foreman tap statusline ...`)\n")
        return 2

    stdin = sys.stdin.read()

    if subcmd == "statusline":
        try:
            import json
            from foreman import limits

            payload = json.loads(stdin) if stdin.strip() else {}
            agent = "antigravity" if args.agent in ("antigravity", "agy") else "claude"
            if agent == "claude":
                windows = limits.parse_claude_statusline(payload)
            else:
                windows = limits.parse_antigravity_statusline(payload)
            if windows:
                limits.record_reading(agent, windows)
            limits.store_session_context(agent, payload)
        except Exception as exc:
            try:
                from foreman import paths, store, timeutil
                store.append_jsonl(paths.events_file(), {
                    "ts": timeutil.now_iso(),
                    "event": "tap_error",
                    "agent": getattr(args, "agent", None),
                    "error": str(exc),
                })
            except Exception:
                pass

        cmd = list(args.cmd or [])
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            return 0
        try:
            proc = subprocess.run(cmd, input=stdin, text=True)
            return proc.returncode
        except Exception as exc:
            sys.stderr.write("foreman tap: failed to run command: %s\n" % exc)
            return 127

    return 0
