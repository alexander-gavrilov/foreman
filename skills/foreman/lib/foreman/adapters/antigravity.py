"""Antigravity (agy) hook payloads: camelCase, event name passed as an argument (integration-facts 2)."""
from foreman.adapters import as_text, blank, shell_command

EVENTS = {"SessionStart": "session_start", "PreInvocation": "prompt", "PreToolUse": "pre_tool",
          "PostToolUse": "post_tool", "Stop": "stop"}


def normalise(event, payload):
    payload = payload if isinstance(payload, dict) else {}
    n = blank(event)
    n["kind"] = EVENTS.get(event)
    n["agent_session_id"] = as_text(payload.get("conversationId"))
    paths = payload.get("workspacePaths")
    if isinstance(paths, list) and paths and isinstance(paths[0], str):
        n["cwd"] = paths[0]
    n["transcript_path"] = as_text(payload.get("transcriptPath"))
    n["model"] = as_text(payload.get("modelName"))
    n["prompt"] = as_text(payload.get("lastUserInput"))
    call = payload.get("toolCall")
    if isinstance(call, dict):
        n["tool_name"] = as_text(call.get("name"))
        n["tool_input"] = call.get("args") if isinstance(call.get("args"), dict) else None
        n["command"] = shell_command(n["tool_input"])
    if event == "Stop":
        n["fully_idle"] = bool(payload.get("fullyIdle", True))
        n["reason"] = as_text(payload.get("terminationReason"))
    return n


def render_deny(reason):
    return {"decision": "deny", "reason": reason}


def render_continue(digest):
    return {"decision": "continue", "reason": digest}


def render_context(text):
    return {"injectSteps": [{"ephemeralMessage": text}]}


def render_pass(event):
    """The answer to a hook that blocks nothing. agy's interactive TUI treats `{}` from a PreToolUse hook as a
    denial with an empty reason (live E4), so an unblocked tool call is handed back to agy's own permission flow
    with `ask`, as Orca's hook does. Foreman never grants permissions, it only denies."""
    return {"decision": "ask"} if event == "PreToolUse" else {}
