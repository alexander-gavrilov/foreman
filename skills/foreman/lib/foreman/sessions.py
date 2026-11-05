"""Session records and per-session inbox queues (architecture §3)."""
import os

from foreman import ids, paths, store, timeutil

STATES = ("starting", "busy", "idle", "waiting_input", "waiting_permission", "stuck", "gone", "rotated",
          "closed")
INACTIVE_STATES = ("closed", "rotated", "gone")


def load_session(sid):
    data = store.read_json(paths.session_file(sid))
    return data if isinstance(data, dict) else None


def save_session(rec):
    store.write_json(paths.session_file(rec["sid"]), rec)
    return rec


def create_session(project, agent, model=None, effort=None, role="worker", owner="orchestrator",
                   purpose=None, title=None, transport=None, agent_session_id=None, cwd=None,
                   transcript_path=None, state="starting", replaces=None):
    sid = ids.new_sid(exists=lambda s: os.path.exists(paths.session_file(s)))
    now = timeutil.now_iso()
    rec = {"sid": sid, "project": project, "agent": agent, "model": model, "effort": effort, "role": role,
           "owner": owner, "purpose": purpose, "title": title or sid,
           "transport": transport or {"kind": None, "handle": None},
           "agent_session_id": agent_session_id, "cwd": cwd, "transcript_path": transcript_path,
           "state": state, "state_since": now, "last_event_at": None,
           "context_tokens": None, "context_at": None, "replaces": replaces, "replaced_by": None,
           "opened_at": now, "closed_at": None}
    return save_session(rec)


def list_sessions(project=None):
    out = []
    try:
        names = os.listdir(paths.sessions_dir())
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        rec = store.read_json(os.path.join(paths.sessions_dir(), name))
        if isinstance(rec, dict) and rec.get("sid") and (project is None or rec.get("project") == project):
            out.append(rec)
    out.sort(key=lambda r: (r.get("opened_at") or "", r["sid"]))
    return out


def _prefer_live(matches):
    if not matches:
        return None
    live = [r for r in matches if r.get("state") not in INACTIVE_STATES]
    pool = live or matches
    return pool[-1]  # list_sessions is oldest-first: newest wins


def find_by_handle(handle, kind=None):
    matches = [r for r in list_sessions()
               if (r.get("transport") or {}).get("handle") == handle
               and (kind is None or (r.get("transport") or {}).get("kind") == kind)]
    return _prefer_live(matches)


def find_by_agent_session_id(agent_session_id):
    matches = [r for r in list_sessions() if r.get("agent_session_id") == agent_session_id]
    return _prefer_live(matches)


def find(ref, project=None):
    """Resolve a sid, title, agent_session_id or transport handle to a session record (or None)."""
    recs = list_sessions(project)
    for match in (lambda r: r["sid"] == ref,
                  lambda r: r.get("title") == ref,
                  lambda r: r.get("agent_session_id") == ref,
                  lambda r: (r.get("transport") or {}).get("handle") == ref):
        found = _prefer_live([r for r in recs if match(r)])
        if found:
            return found
    return None


def update_session(sid, **fields):
    """Locked read-modify-write of selected fields. Raises KeyError for an unknown session."""
    path = paths.session_file(sid)
    if not os.path.exists(path):
        raise KeyError(sid)

    def apply(rec):
        if not isinstance(rec, dict):
            raise KeyError(sid)
        rec.update(fields)
        return rec
    return store.update_json(path, apply, default=None)


def set_state(sid, state, at=None):
    """Change state; state_since moves only when the state actually changes. 'closed' stamps closed_at."""
    if state not in STATES:
        raise ValueError("unknown session state %r" % (state,))
    path = paths.session_file(sid)
    if not os.path.exists(path):
        raise KeyError(sid)
    now = at or timeutil.now_iso()

    def apply(rec):
        if not isinstance(rec, dict):
            raise KeyError(sid)
        if rec.get("state") != state:
            rec["state"] = state
            rec["state_since"] = now
        if state == "closed" and not rec.get("closed_at"):
            rec["closed_at"] = now
        return rec
    return store.update_json(path, apply, default=None)


# ---------------------------------------------------------------- inbox

def inbox_enqueue(sid, text, sender="orchestrator", kind="message"):
    msg = {"id": ids.new_msg_id(), "ts": timeutil.now_iso(), "from": sender, "text": text, "kind": kind,
           "delivered_at": None}
    return store.append_jsonl(paths.inbox_file(sid), msg)


def inbox_all(sid):
    return store.read_jsonl(paths.inbox_file(sid))


def inbox_pending(sid):
    return [m for m in inbox_all(sid) if not m.get("delivered_at")]


def inbox_mark_delivered(sid, msg_ids=None):
    """Mark the given message ids (or every pending message when None) as delivered."""
    stamp = timeutil.now_iso()
    wanted = None if msg_ids is None else set(msg_ids)

    def fn(rows):
        for m in rows:
            if not m.get("delivered_at") and (wanted is None or m.get("id") in wanted):
                m["delivered_at"] = stamp
        return rows
    path = paths.inbox_file(sid)
    if not os.path.exists(path):
        return []
    return store.rewrite_jsonl(path, fn)
