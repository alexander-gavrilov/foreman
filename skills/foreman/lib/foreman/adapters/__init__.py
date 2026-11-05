"""Agent adapters (architecture 4a): turn a native hook payload into one normalised event and render
foreman's decisions in the agent's own output dialect.

A normalised event is a dict with these keys (all may be None except `kind`/`native_event`):
  kind, native_event, agent_session_id, cwd, transcript_path, model, prompt, tool_name, tool_input,
  command (shell command text when the tool is a shell), last_assistant_message, stop_hook_active,
  fully_idle, reason, source (SessionStart: startup|resume|clear|compact|fork).
`kind` is one of session_start, prompt, pre_tool, post_tool, stop, wait_input, wait_permission,
session_end, or None for an event foreman does not track.
"""
import importlib

NAMES = ("claude", "antigravity", "codex")


def get(agent):
    if agent not in NAMES:
        raise ValueError("unknown agent %r (known: %s)" % (agent, ", ".join(NAMES)))
    return importlib.import_module("foreman.adapters." + agent)


def blank(native_event):
    return {"kind": None, "native_event": native_event, "agent_session_id": None, "cwd": None,
            "transcript_path": None, "model": None, "prompt": None, "tool_name": None, "tool_input": None,
            "command": None, "last_assistant_message": None, "stop_hook_active": False, "fully_idle": None,
            "reason": None, "source": None}


def as_text(value):
    return value if isinstance(value, str) and value else None


def shell_command(tool_input):
    """The command text of a shell-like tool input: {"command": "ls"} / {"command": ["bash","-lc","ls"]} /
    {"cmd": ...} / agy {"CommandLine": ...}; None when there is none."""
    if not isinstance(tool_input, dict):
        return None
    for key in ("command", "cmd", "CommandLine", "commandLine"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
            argv = list(value)
            # ["bash", "-lc", "<script>"] -> the script itself
            if len(argv) >= 3 and argv[0] in ("bash", "sh", "zsh", "/bin/bash", "/bin/sh", "/bin/zsh") \
                    and argv[1] in ("-c", "-lc", "-ic", "-l", "-i") and argv[2:]:
                return " ".join(argv[2:])
            return " ".join(argv)
    return None
