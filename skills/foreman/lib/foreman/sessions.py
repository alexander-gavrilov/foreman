"""Session records and per-session inbox queues (architecture §3)."""
import json
import os

from foreman import ids, paths, store, timeutil
from foreman.errors import ForemanError

STATES = ("starting", "busy", "idle", "waiting_input", "waiting_permission", "stuck", "gone")  # activity only
INACTIVE_STATES = ("gone",)  # closed / rotated are lifecycles (below), not activity states
LIFECYCLES = ("active", "rotated", "closed")


class AmbiguousSession(ForemanError):
    """A reference matched live sessions of several projects; the message lists the candidates."""

    def __init__(self, ref, candidates):
        self.candidates = candidates
        lines = ", ".join("%s (%s, project %s)" % (r.get("title") or r["sid"], r["sid"], r.get("project"))
                          for r in candidates)
        super().__init__("%r matches several sessions: %s; use the sid or --project" % (ref, lines), 2)


def load_session(sid):
    path = paths.session_file(sid)
    data = store.read_json(path)
    if data is None:
        return None
    if not isinstance(data, dict):
        store.quarantine(path, "not a JSON object")
    return data


def save_session(rec):
    store.write_json(paths.session_file(rec["sid"]), rec)
    return rec


def create_session(project, agent, model=None, effort=None, role="worker", owner="orchestrator",
                   purpose=None, title=None, transport=None, agent_session_id=None, cwd=None,
                   transcript_path=None, state="starting", replaces=None, lifecycle="active"):
    sid = ids.new_sid(exists=lambda s: os.path.exists(paths.session_file(s)))
    now = timeutil.now_iso()
    rec = {"sid": sid, "project": project, "agent": agent, "model": model, "effort": effort, "role": role,
           "owner": owner, "purpose": purpose, "title": title or sid,
           "transport": transport or {"kind": None, "handle": None},
           "agent_session_id": agent_session_id, "cwd": cwd, "transcript_path": transcript_path,
           "state": state, "lifecycle": lifecycle, "state_since": now, "last_event_at": None,
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
        try:
            rec = load_session(name[:-len(".json")])
        except (store.StateCorrupt, ValueError):
            continue  # moved aside; keep listing the healthy sessions
        if rec and rec.get("sid") and (project is None or rec.get("project") == project):
            out.append(rec)
    out.sort(key=lambda r: (r.get("opened_at") or "", r["sid"]))
    return out


def is_live(rec):
    """Open, not gone and not rotated out: the session that currently does the work."""
    return rec.get("state") not in INACTIVE_STATES and rec.get("lifecycle") not in ("closed", "rotated")


_is_live = is_live


def _prefer_live(matches):
    if not matches:
        return None
    live = [r for r in matches if _is_live(r)]
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


def _live(recs):
    return [r for r in recs if _is_live(r)]


def find(ref, project=None):
    """Resolve a sid, title, agent_session_id or transport handle to a session record (or None).

    sid / agent_session_id / handle are globally unique and looked up everywhere. A title is scoped to
    `project` when given; without a project it must identify one project's session, otherwise
    AmbiguousSession lists the candidates (live sessions only count, inactive duplicates are ignored)."""
    recs = list_sessions()
    found = _prefer_live([r for r in recs if r["sid"] == ref])
    if found:
        return found
    titled = [r for r in recs if r.get("title") == ref and (project is None or r.get("project") == project)]
    if project is None:
        live = _live(titled)
        projs = sorted({r.get("project") for r in live})
        if len(projs) > 1:
            raise AmbiguousSession(ref, live)
    found = _prefer_live(titled)
    if found:
        return found
    for match in (lambda r: r.get("agent_session_id") == ref,
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


def is_open(rec):
    """False once the session's lifecycle is closed (records without the field are active). Activity
    (`state`) is separate: a rotated session is open and its busy/idle state is still tracked."""
    return rec.get("lifecycle") != "closed"


def set_lifecycle(sid, lifecycle, at=None):
    """active | rotated | closed. 'closed' stamps closed_at."""
    if lifecycle not in LIFECYCLES:
        raise ValueError("unknown session lifecycle %r" % (lifecycle,))
    path = paths.session_file(sid)
    if not os.path.exists(path):
        raise KeyError(sid)
    now = at or timeutil.now_iso()

    def apply(rec):
        if not isinstance(rec, dict):
            raise KeyError(sid)
        rec["lifecycle"] = lifecycle
        if lifecycle == "closed" and not rec.get("closed_at"):
            rec["closed_at"] = now
        return rec
    return store.update_json(path, apply, default=None)


def set_state(sid, state, at=None):
    """Change activity state; state_since moves only when the state actually changes."""
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
        return rec
    return store.update_json(path, apply, default=None)


# ---------------------------------------------------------------- inbox

def inbox_enqueue(sid, text, sender="orchestrator", kind="message", key=None, msg_id=None, extra=None,
                  replace=False):
    """Queue a message. With `key`, a message with the same key that is still undelivered suppresses the new
    one (returns None); with `replace=True` it is updated in place to the new text instead, so the reader
    sees the latest state. `msg_id` lets the caller embed the id in the text before queueing; `extra` adds
    fields (e.g. {"asker": sid} on a question)."""
    msg = {"id": msg_id or ids.new_msg_id(), "ts": timeutil.now_iso(), "from": sender, "text": text,
           "kind": kind, "delivered_at": None}
    if extra:
        msg.update(extra)
    if key is None:
        return store.append_jsonl(paths.inbox_file(sid), msg)
    msg["key"] = key
    result = {}

    def fn(rows):
        for row in rows:
            if row.get("key") == key and not row.get("delivered_at"):
                if replace:
                    row.update(text=text, ts=msg["ts"], **({"from": sender} if sender else {}))
                return rows
        rows.append(msg)
        result["queued"] = msg
        return rows
    store.rewrite_jsonl(paths.inbox_file(sid), fn)
    return result.get("queued")


def inbox_all(sid):
    return store.read_jsonl(paths.inbox_file(sid))


def inbox_pending(sid):
    """Read-only view of undelivered messages. To consume them use inbox_claim."""
    return [m for m in inbox_all(sid) if not m.get("delivered_at")]


def inbox_claim(sid, limit=None, claimer=None):
    """Atomically select pending messages and stamp them delivered (claimed), under ONE lock, so that
    concurrent deliverers (Stop hook, send, watch loop, deliver) can never both get the same message.
    Returns the claimed messages (oldest first). If delivery then fails, give them back with inbox_release."""
    path = paths.inbox_file(sid)
    if not os.path.exists(path):
        return []
    stamp = timeutil.now_iso()
    claimed = []

    def fn(rows):
        for m in rows:
            if m.get("delivered_at"):
                continue
            if limit is not None and len(claimed) >= limit:
                break
            m["delivered_at"] = stamp
            if claimer:
                m["claimed_by"] = claimer
            claimed.append(dict(m))
        return rows
    store.rewrite_jsonl(path, fn)
    return claimed


def inbox_release(sid, msg_ids):
    """Undo a claim for messages whose delivery failed: they become pending again."""
    wanted = set(msg_ids)

    def fn(rows):
        for m in rows:
            if m.get("id") in wanted:
                m["delivered_at"] = None
                m.pop("claimed_by", None)
        return rows
    path = paths.inbox_file(sid)
    if os.path.exists(path):
        store.rewrite_jsonl(path, fn)


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
