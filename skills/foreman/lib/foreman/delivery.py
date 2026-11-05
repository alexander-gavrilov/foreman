"""Deliver queued inbox messages to idle sessions through their transport (spec 4.7)."""
from foreman import digest, sessions
from foreman.transports import get_transport
from foreman.transports.base import TerminalGone


def inspect(rec):
    """-> (status, transport). status: "none" (no usable transport recorded), "gone", "shell" (the terminal
    exists but hosts no agent), "idle" or "busy"."""
    tr = rec.get("transport") or {}
    if not tr.get("kind") or not tr.get("handle"):
        return "none", None
    try:
        transport = get_transport(tr["kind"])
        infos = transport.list()
    except Exception:
        return "none", None
    for info in infos:
        if info.handle == tr["handle"]:
            if not info.agent:
                return "shell", transport
            idle = info.idle if info.idle is not None else rec.get("state") == "idle"
            return ("idle" if idle else "busy"), transport
    return "gone", transport


def deliver_pending(sid):
    """Deliver pending inbox messages of session `sid` if its terminal is idle: claims them (so no other
    deliverer can send the same ones), types the digest, and gives them back if typing fails. Returns
    "delivered", "queued" (busy, plain shell or no transport), "nothing" (no pending messages) or
    "terminal_gone" (the session is then marked gone)."""
    rec = sessions.load_session(sid)
    if rec is None:
        raise KeyError(sid)
    if not sessions.inbox_pending(sid):
        return "nothing"
    if not sessions.is_open(rec):
        return "terminal_gone"
    status, transport = inspect(rec)
    if status == "gone":
        sessions.set_state(sid, "gone")
        return "terminal_gone"
    if status != "idle":
        return "queued"
    claimed = sessions.inbox_claim(sid, claimer="deliver")
    if not claimed:
        return "nothing"  # another deliverer got there first
    try:
        transport.send(rec["transport"]["handle"], digest.flatten(digest.format_digest(claimed)))
    except TerminalGone:
        sessions.inbox_release(sid, [m["id"] for m in claimed])
        sessions.set_state(sid, "gone")
        return "terminal_gone"
    except Exception:
        sessions.inbox_release(sid, [m["id"] for m in claimed])
        return "queued"
    return "delivered"
