"""Codex hook payloads: Claude layout (integration-facts section 3). Unverified at runtime in this build
(no login on the build machine); the shapes follow the documentation."""
from foreman.adapters import claude, shell_command

EVENTS = {"SessionStart": "session_start", "UserPromptSubmit": "prompt", "PreToolUse": "pre_tool",
          "PostToolUse": "post_tool", "Stop": "stop", "PermissionRequest": "wait_permission",
          "SessionEnd": "session_end"}


def normalise(event, payload):
    n = claude.normalise(event, payload)
    n["kind"] = EVENTS.get(n["native_event"])  # no Notification event in Codex
    if n["command"] is None and n["tool_input"]:
        n["command"] = shell_command(n["tool_input"])
    return n


render_deny = claude.render_deny
render_continue = claude.render_continue
render_context = claude.render_context
