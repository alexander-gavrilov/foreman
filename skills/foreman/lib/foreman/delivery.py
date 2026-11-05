"""Deliver queued inbox messages to idle sessions through their transport (spec 4.7)."""
from foreman import digest, sessions
from foreman.transports import get_transport
from foreman.transports.base import TerminalGone


def _terminal_state(rec):
    """-> ("none"|"gone"|"idle"|"busy", transport). "none": no usable transport is recorded."""
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
            idle = info.idle if info.idle is not None else rec.get("state") == "idle"
            return ("idle" if idle else "busy"), transport
    return "gone", transport


def deliver_pending(sid):
    """Deliver pending inbox messages of session `sid` if its terminal is idle: types the digest, marks
    the messages delivered. Returns "delivered", "queued" (busy or no transport), "nothing" (no pending
    messages) or "terminal_gone" (the session is then marked gone)."""
    rec = sessions.load_session(sid)
    if rec is None:
        raise KeyError(sid)
    pending = sessions.inbox_pending(sid)
    if not pending:
        return "nothing"
    if rec.get("state") == "closed":
        return "terminal_gone"
    state, transport = _terminal_state(rec)
    if state == "gone":
        sessions.set_state(sid, "gone")
        return "terminal_gone"
    if state != "idle":
        return "queued"
    try:
        transport.send(rec["transport"]["handle"], digest.flatten(digest.format_digest(pending)))
    except TerminalGone:
        sessions.set_state(sid, "gone")
        return "terminal_gone"
    sessions.inbox_mark_delivered(sid, [m["id"] for m in pending])
    return "delivered"
