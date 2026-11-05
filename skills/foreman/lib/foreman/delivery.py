"""Deliver queued inbox messages to idle sessions (owner: WP1). Interface stub."""


def deliver_pending(sid):
    """Deliver pending inbox messages of session `sid` if it is idle. Returns "delivered", "queued",
    "nothing" or "terminal_gone"."""
    return "nothing"
