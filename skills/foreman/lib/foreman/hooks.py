"""The hook pipeline (architecture 3, 4): normalise -> bind -> guard -> update state -> side effects -> output.
A hook never breaks the agent: whatever goes wrong inside is logged and the output is `{}`."""
import json
import os

from foreman import (adapters, context, digest, guard, paths, projects, sessions, store, timeutil,
                     wake)
from foreman.messaging import HANDLE_ENV

EVENTS_MAX_BYTES = 5 * 1024 * 1024
WAKE_EXCERPT = 200
KEEP_OPEN_REASONS = ("clear", "resume")  # SessionEnd reasons after which the terminal keeps a session
TAKEOVER_SOURCES = ("clear", "resume", "compact")
BUSY_KINDS = ("prompt", "pre_tool", "post_tool")


# ---------------------------------------------------------------- events.jsonl

def _append_event(row):
    path = paths.events_file()
    try:
        if os.path.getsize(path) > EVENTS_MAX_BYTES:
            os.replace(path, path + ".1")
    except OSError:
        pass
    store.append_jsonl(path, dict({"ts": timeutil.now_iso()}, **row))


def _log_error(agent, event, exc):
    try:
        _append_event({"kind": "hook_error", "agent": agent, "event": event,
                       "error": "%s: %s" % (type(exc).__name__, exc)})
    except Exception:
        pass


# ---------------------------------------------------------------- binding (architecture 3)

def _terminal_kind(env_key):
    if env_key == "HERDR_PANE_ID":
        return "herdr"
    if env_key == "ORCA_TERMINAL_HANDLE":
        return "orca"
    return os.environ.get("FOREMAN_TRANSPORT") or "fake"


def _prefer_live(recs):
    live = [r for r in recs if r.get("state") not in sessions.INACTIVE_STATES]
    pool = live or recs
    return pool[-1] if pool else None


def _bind(agent, norm, env):
    """-> (record, created). (None, False) when the event belongs to no foreman session."""
    asid = norm["agent_session_id"]
    if asid:
        rec = _prefer_live([r for r in sessions.list_sessions()
                            if r.get("agent") == agent and r.get("agent_session_id") == asid])
        if rec:
            return rec, False
    handle_env = [(k, env[k]) for k in HANDLE_ENV if env.get(k)]
    for _, handle in handle_env:
        rec = sessions.find_by_handle(handle)
        if rec and rec.get("agent") == agent:
            return rec, False
    proj = projects.project_for_path(norm["cwd"]) if norm["cwd"] else None
    if proj is None:
        return None, False
    transport = {"kind": None, "handle": None}
    for key, handle in handle_env:
        holder = sessions.find_by_handle(handle)
        if holder is None or holder.get("state") in sessions.INACTIVE_STATES:
            transport = {"kind": _terminal_kind(key), "handle": handle}
            break
    rec = sessions.create_session(
        proj["slug"], agent, model=norm["model"], role="worker", owner="user", transport=transport,
        agent_session_id=asid, cwd=norm["cwd"], transcript_path=norm["transcript_path"], state="starting")
    return rec, True


# ---------------------------------------------------------------- state

def _terminal_dead(rec):
    tr = rec.get("transport") or {}
    if not tr.get("kind") or not tr.get("handle"):
        return False
    try:
        from foreman.transports import get_transport
        return not get_transport(tr["kind"]).alive(tr["handle"])
    except Exception:
        return False


def _next_state(rec, norm, created):
    kind = norm["kind"]
    if rec.get("state") == "rotated":
        return None  # a rotated session stays shown as rotated
    if kind in BUSY_KINDS:
        return "busy"
    if kind == "stop":
        return "busy" if norm["fully_idle"] is False else "idle"
    if kind in ("wait_input", "wait_permission"):
        return "waiting_" + kind[len("wait_"):]
    if kind == "session_start":
        return "idle" if created or rec.get("state") in ("closed", "gone") else None
    if kind == "session_end":
        if norm["reason"] in KEEP_OPEN_REASONS:
            return None
        return "gone" if _terminal_dead(rec) else "closed"
    return None


def _field_updates(rec, norm):
    upd = {"last_event_at": timeutil.now_iso()}
    asid = norm["agent_session_id"]
    if asid and asid != rec.get("agent_session_id"):
        takeover = (rec.get("agent_session_id") is None or
                    (norm["kind"] == "session_start" and
                     (norm["source"] in TAKEOVER_SOURCES or rec.get("state") in ("closed", "gone", "starting"))))
        if takeover:
            upd["agent_session_id"] = asid
    if norm["transcript_path"] and norm["transcript_path"] != rec.get("transcript_path"):
        upd["transcript_path"] = norm["transcript_path"]
    if norm["cwd"] and not rec.get("cwd"):
        upd["cwd"] = norm["cwd"]
    if norm["model"] and not rec.get("model"):
        upd["model"] = norm["model"]
    return upd


def _refresh_context(agent, rec, norm, upd):
    """Claude: tail of the transcript. Other agents stay unknown (None) unless a statusline tap knows."""
    if agent != "claude" or norm["kind"] not in ("stop", "session_start"):
        return
    path = norm["transcript_path"] or rec.get("transcript_path")
    tokens = context.claude_context_tokens(path)
    if tokens is not None:
        upd["context_tokens"] = tokens
        upd["context_at"] = timeutil.now_iso()


# ---------------------------------------------------------------- side effects

def _wake_text(rec, norm):
    who = rec.get("agent") or "?"
    if rec.get("model"):
        who += "/" + rec["model"]
    text = norm["last_assistant_message"] or context.last_assistant_text(
        norm["transcript_path"] or rec.get("transcript_path"))
    line = "%s (%s, %s) finished a turn" % (rec.get("title") or rec["sid"], rec["sid"], who)
    return line + (": " + context.excerpt(text, WAKE_EXCERPT) if text else "")


def _on_stop(agent, ad, rec, norm):
    """-> hook output for a Stop: the digest when messages are queued, else {} (and wake the orchestrator
    when a worker the orchestrator owns finished a turn)."""
    sid = rec["sid"]
    pending = sessions.inbox_pending(sid)
    if pending and not norm["stop_hook_active"]:
        sessions.inbox_mark_delivered(sid, [m["id"] for m in pending])
        sessions.set_state(sid, "busy")  # the agent continues with the digest as its next input
        return ad.render_continue(digest.format_digest(pending))
    if rec.get("role") == "worker" and rec.get("owner") == "orchestrator":
        try:
            wake.queue_for_orchestrator(rec["project"], _wake_text(rec, norm), sender="foreman", kind="wake",
                                        key="stop:" + sid)
        except Exception as exc:
            _log_error(agent, "wake", exc)
    return {}


def _process(agent, event, payload, env):
    try:
        ad = adapters.get(agent)
    except ValueError:
        return {}
    norm = ad.normalise(event, payload)
    if not norm["kind"]:
        return {}
    os.makedirs(paths.sessions_dir(), exist_ok=True)
    with store.locked(os.path.join(paths.sessions_dir(), "_bind")):
        rec, created = _bind(agent, norm, env)
    if rec is None:
        return {}
    sid = rec["sid"]
    out, blocked = {}, None
    if norm["kind"] == "pre_tool" and rec.get("role") == "orchestrator":
        blocked = guard.check_tool(norm, project=rec.get("project"))
        if blocked:
            out = ad.render_deny(blocked)

    upd = _field_updates(rec, norm)
    _refresh_context(agent, rec, norm, upd)
    sessions.update_session(sid, **upd)
    rec = dict(rec, **upd)
    state = _next_state(rec, norm, created)
    if state:
        sessions.set_state(sid, state)

    if norm["kind"] == "stop" and norm["fully_idle"] is not False:
        out = _on_stop(agent, ad, rec, norm)

    final = sessions.load_session(sid) or {}
    _append_event({"sid": sid, "project": rec.get("project"), "agent": agent, "event": norm["native_event"],
                   "kind": norm["kind"], "tool": norm["tool_name"], "blocked": bool(blocked),
                   "state": final.get("state")})
    return out


def run_hook(agent, event, raw, env=None):
    """Process one hook invocation (`raw` = the stdin text). Returns the JSON-able output; never raises."""
    env = os.environ if env is None else env
    try:
        try:
            payload = json.loads(raw) if raw and raw.strip() else {}
        except ValueError:
            payload = {}
        out = _process(agent, event, payload if isinstance(payload, dict) else {}, env)
        return out if isinstance(out, dict) else {}
    except Exception as exc:
        _log_error(agent, event, exc)
        return {}
