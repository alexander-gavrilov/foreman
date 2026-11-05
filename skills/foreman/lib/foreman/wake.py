"""Wake the project's orchestrator: queue a one-line event in its inbox (spec 4.6)."""
from foreman import delivery, projects, sessions


def _usable(rec):
    return sessions.is_open(rec) and rec.get("state") != "gone"


def find_orchestrator(project):
    """The project's orchestrator session record (project.json `orchestrator` is its sid), else the newest
    live session with role orchestrator. None when there is none."""
    proj = projects.load_project(project)
    ref = (proj or {}).get("orchestrator")
    if isinstance(ref, dict):
        ref = ref.get("sid")
    if isinstance(ref, str):
        rec = sessions.load_session(ref)
        if rec and _usable(rec):
            return rec
    live = [r for r in sessions.list_sessions(project) if r.get("role") == "orchestrator" and _usable(r)]
    return live[-1] if live else None


def queue_for_orchestrator(project, text, sender="foreman", kind="wake", key=None):
    """Queue `text` for the project's orchestrator and, if both its record and its terminal say idle,
    type it now (a Stop hook would never fire for a session that is already idle). Returns the queued
    message, or None when there is no orchestrator or `key` deduplicated it."""
    orch = find_orchestrator(project)
    if orch is None:
        return None
    msg = sessions.inbox_enqueue(orch["sid"], text, sender=sender, kind=kind, key=key)
    if msg is not None and orch.get("state") == "idle":
        try:
            delivery.deliver_pending(orch["sid"])
        except Exception:
            pass
    return msg


def wake(project, text, key=None):
    """Queue a one-line event for the orchestrator of `project`. `key` deduplicates: the same key is queued
    at most once until delivered. Returns True if queued."""
    return queue_for_orchestrator(project, text, sender="foreman", kind="wake", key=key) is not None
