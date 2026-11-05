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


def queue_for_orchestrator(project, text, sender="foreman", kind="wake", key=None, background=False):
    """Queue `text` for the project's orchestrator and, if both its record and its terminal say idle,
    deliver it now (a Stop hook would never fire for a session that is already idle). With `key`, a pending
    message under the same key is updated to the new text. `background=True` (hooks) delivers from a
    detached child process instead of in the caller. Returns the newly queued message, or None when there
    is no orchestrator or the key matched a pending message."""
    orch = find_orchestrator(project)
    if orch is None:
        return None
    msg = sessions.inbox_enqueue(orch["sid"], text, sender=sender, kind=kind, key=key, replace=key is not None)
    if orch.get("state") == "idle":
        try:
            if background:
                delivery.spawn_deliver(orch["sid"])
            else:
                delivery.deliver_pending(orch["sid"])
        except Exception:
            pass
    return msg


def wake(project, text, key=None):
    """Queue a one-line event for the orchestrator of `project`. `key` makes it a single pending line: a
    newer text replaces the pending one. Returns True if a new message was queued."""
    return queue_for_orchestrator(project, text, sender="foreman", kind="wake", key=key) is not None
