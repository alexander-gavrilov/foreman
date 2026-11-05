"""Deliver queued inbox messages to idle sessions (spec §4.7)."""
from foreman import sessions
from foreman.transports import get_transport
from foreman.transports.base import TerminalGone


def deliver_pending(sid):
    """Deliver pending inbox messages of session `sid` if it is idle.

    Uses sessions.inbox_claim so messages are claimed atomically and never delivered twice.
    """
    rec = sessions.load_session(sid)
    if not rec:
        return "nothing"
    if rec.get("lifecycle") == "closed" or rec.get("state") == "closed":
        return "terminal_gone"
    if rec.get("state") != "idle":
        return "queued"
    if not sessions.inbox_pending(sid):
        return "nothing"

    claimed = sessions.inbox_claim(sid, claimer="deliver")
    if not claimed:
        return "nothing"

    tr = rec.get("transport") or {}
    kind = tr.get("kind")
    handle = tr.get("handle")
    if not kind or not handle:
        sessions.inbox_release(sid, [m["id"] for m in claimed])
        return "queued"

    try:
        t = get_transport(kind)
        text = "\n".join(m.get("text", "") for m in claimed)
        t.send(handle, text)
        return "delivered"
    except TerminalGone:
        sessions.inbox_release(sid, [m["id"] for m in claimed])
        sessions.set_state(sid, "gone")
        return "terminal_gone"
    except Exception:
        sessions.inbox_release(sid, [m["id"] for m in claimed])
        return "queued"
