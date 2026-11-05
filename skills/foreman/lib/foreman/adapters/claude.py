"""Claude Code hook payloads (integration-facts section 1)."""
from foreman.adapters import as_text, blank, shell_command

EVENTS = {"SessionStart": "session_start", "UserPromptSubmit": "prompt", "PreToolUse": "pre_tool",
          "PostToolUse": "post_tool", "Stop": "stop", "PermissionRequest": "wait_permission",
          "SessionEnd": "session_end"}
# Notification.notification_type -> kind. idle_prompt is deliberately absent: the session is already
# idle (its Stop was seen), so the nudge adds nothing.
NOTIFICATIONS = {"permission_prompt": "wait_permission", "elicitation_dialog": "wait_input"}


def _notification_kind(payload):
    ntype = payload.get("notification_type")
    if ntype in NOTIFICATIONS:
        return NOTIFICATIONS[ntype]
    if ntype:
        return None
    message = (payload.get("message") or "").lower()
    if "permission" in message:
        return "wait_permission"
    return None


def normalise(event, payload):
    payload = payload if isinstance(payload, dict) else {}
    event = event or payload.get("hook_event_name")
    n = blank(event)
    n["kind"] = _notification_kind(payload) if event == "Notification" else EVENTS.get(event)
    n["agent_session_id"] = as_text(payload.get("session_id"))
    n["cwd"] = as_text(payload.get("cwd"))
    n["transcript_path"] = as_text(payload.get("transcript_path"))
    n["model"] = as_text(payload.get("model")) if isinstance(payload.get("model"), str) else None
    n["prompt"] = as_text(payload.get("prompt"))
    n["tool_name"] = as_text(payload.get("tool_name"))
    n["tool_input"] = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else None
    n["command"] = shell_command(n["tool_input"])
    n["last_assistant_message"] = as_text(payload.get("last_assistant_message"))
    n["stop_hook_active"] = bool(payload.get("stop_hook_active"))
    n["reason"] = as_text(payload.get("reason"))
    n["source"] = as_text(payload.get("source"))
    n["agent_id"] = as_text(payload.get("agent_id"))
    return n


def render_deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def render_continue(digest):
    """Stop hook: block the stop so the agent continues with `digest` as its next input."""
    return {"decision": "block", "reason": digest}


def render_context(text):
    """SessionStart: add `text` to the agent's context."""
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
