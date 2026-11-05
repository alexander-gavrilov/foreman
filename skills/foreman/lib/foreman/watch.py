"""Watch loop pass and monitoring (architecture §8, spec §4.6)."""
import hashlib
import os

from foreman import config, delivery, limits, notify, paths, projects, sessions, store, timeutil, transports, wake

_CONDITION_PREFIXES = (
    "gone:",
    "stuck:",
    "waiting:",
    "context_soft:",
    "context_hard:",
    "limit_warn:",
    "limit_stop:",
)


def run_pass(interval_s=60):
    """One zero-token watch pass (architecture section 8). Returns a summary dict."""
    now_iso = timeutil.now_iso()

    # 1. Heartbeat
    heartbeat = {
        "pid": os.getpid(),
        "last_run_at": now_iso,
        "interval_s": interval_s,
    }
    store.write_json(paths.watch_heartbeat_file(), heartbeat)

    # Load watch state
    state = store.read_json(paths.watch_state_file(), default={})
    if not isinstance(state, dict):
        state = {}
    dedupe = set(state.get("dedupe") or [])
    active_conditions = set()

    summary = {
        "wakes": 0,
        "deliveries": 0,
        "conditions": [],
    }

    # 2. Transports check
    all_sessions = sessions.list_sessions()
    non_closed = [
        s for s in all_sessions
        if sessions.is_open(s)
    ]

    transport_kinds = {
        (s.get("transport") or {}).get("kind")
        for s in non_closed
    } - {None, ""}

    live_handles_by_kind = {}
    for kind in transport_kinds:
        try:
            t = transports.get_transport(kind)
            term_list = t.list()
            live_handles_by_kind[kind] = {item.handle for item in term_list}
        except Exception:
            live_handles_by_kind[kind] = set()

    # 3. Per non-closed session checks
    for s in non_closed:
        sid = s["sid"]
        title = s.get("title") or sid
        project = s.get("project")
        tr = s.get("transport") or {}
        kind = tr.get("kind")
        handle = tr.get("handle")
        is_rotated = (s.get("lifecycle") == "rotated")

        # Terminal gone (watched for all non-closed sessions, including rotated)
        if kind and kind in live_handles_by_kind and handle and handle not in live_handles_by_kind[kind]:
            cond = "gone:" + sid
            active_conditions.add(cond)
            if s.get("state") != "gone":
                # Mark state gone, but never change lifecycle of a rotated session to closed
                sessions.set_state(sid, "gone")
            if cond not in dedupe:
                wake.wake(project, "terminal gone: " + title)
                dedupe.add(cond)
                summary["wakes"] += 1
                summary["conditions"].append(cond)
            continue

        # Rotated sessions are watched only for terminal gone, never for stuck/waiting/context
        if is_rotated:
            continue

        # If not gone, check stuck
        if s.get("state") == "busy":
            last_ev = s.get("last_event_at") or s.get("state_since")
            age = timeutil.age_seconds(last_ev)
            stuck_thresh = config.get("thresholds.stuck_after_s", 1200)
            if age is not None and age >= stuck_thresh:
                cond = "stuck:" + sid
                active_conditions.add(cond)
                if cond not in dedupe:
                    wake.wake(project, "session stuck: " + title)
                    dedupe.add(cond)
                    summary["wakes"] += 1
                    summary["conditions"].append(cond)

        # Waiting too long
        cur_state = s.get("state") or ""
        if cur_state.startswith("waiting_"):
            age = timeutil.age_seconds(s.get("state_since"))
            waiting_thresh = config.get("thresholds.waiting_after_s", 900)
            if age is not None and age >= waiting_thresh:
                cond = "waiting:" + sid
                active_conditions.add(cond)
                if cond not in dedupe:
                    wake.wake(project, "session waiting too long: " + title)
                    dedupe.add(cond)
                    summary["wakes"] += 1
                    summary["conditions"].append(cond)

        # Context thresholds
        ctx_tokens = s.get("context_tokens")
        if ctx_tokens is not None:
            c_soft = config.get("thresholds.context_soft", 150000)
            c_hard = config.get("thresholds.context_hard", 250000)
            if ctx_tokens >= c_hard:
                cond_hard = "context_hard:" + sid
                cond_soft = "context_soft:" + sid
                active_conditions.add(cond_hard)
                active_conditions.add(cond_soft)
                if cond_hard not in dedupe:
                    wake.wake(project, "session context hard threshold reached: %s (%d tokens)" % (title, ctx_tokens))
                    dedupe.add(cond_hard)
                    dedupe.add(cond_soft)
                    summary["wakes"] += 1
                    summary["conditions"].append(cond_hard)
            elif ctx_tokens >= c_soft:
                cond_soft = "context_soft:" + sid
                active_conditions.add(cond_soft)
                if cond_soft not in dedupe:
                    wake.wake(project, "session context soft threshold reached: %s (%d tokens)" % (title, ctx_tokens))
                    dedupe.add(cond_soft)
                    summary["wakes"] += 1
                    summary["conditions"].append(cond_soft)

    # 4. Limits crossing limit_warn / limit_stop
    limit_warn = config.get("thresholds.limit_warn", 70)
    limit_stop = config.get("thresholds.limit_stop", 90)
    all_projs = projects.list_projects()

    for prov in ("claude", "antigravity"):
        p_limits = limits.load_provider_limits(prov)
        vals = [
            w["used_pct"]
            for w in p_limits.values()
            if isinstance(w, dict) and w.get("used_pct") is not None
        ]
        max_pct = max(vals) if vals else 0

        if max_pct >= limit_stop:
            cond_stop = "limit_stop:" + prov
            cond_warn = "limit_warn:" + prov
            active_conditions.add(cond_stop)
            active_conditions.add(cond_warn)
            if cond_stop not in dedupe:
                msg = "%s limit stop threshold reached (%d%%)" % (prov.capitalize(), max_pct)
                for p in all_projs:
                    wake.wake(p["slug"], msg)
                    summary["wakes"] += 1
                notify.notify(None, msg)

                # Queue wind-down message for every running worker session of that provider
                wind_down_text = "Лимит %s ≥90%%: wind down and hand off at your next idle point" % prov
                for ws in all_sessions:
                    agent = ws.get("agent")
                    agent_matches = (agent == prov or (prov == "antigravity" and agent == "agy"))
                    is_worker = (ws.get("role") == "worker")
                    is_running = (sessions.is_open(ws) and ws.get("state") != "gone")
                    if agent_matches and is_worker and is_running:
                        sessions.inbox_enqueue(ws["sid"], wind_down_text, sender="foreman")

                dedupe.add(cond_stop)
                dedupe.add(cond_warn)
                summary["conditions"].append(cond_stop)
        elif max_pct >= limit_warn:
            cond_warn = "limit_warn:" + prov
            active_conditions.add(cond_warn)
            if cond_warn not in dedupe:
                msg = "%s limit warn threshold reached (%d%%)" % (prov.capitalize(), max_pct)
                for p in all_projs:
                    wake.wake(p["slug"], msg)
                    summary["wakes"] += 1
                notify.notify(None, msg)
                dedupe.add(cond_warn)
                summary["conditions"].append(cond_warn)

    # 5. Clear dedupe keys for conditions that have cleared
    dedupe = {
        c for c in dedupe
        if not any(c.startswith(pfx) for pfx in _CONDITION_PREFIXES) or c in active_conditions
    }

    # 6. Deliver pending inbox messages for idle sessions
    for s in all_sessions:
        if s.get("state") == "idle" and sessions.inbox_pending(s["sid"]):
            res = delivery.deliver_pending(s["sid"])
            if res in ("delivered", "queued"):
                summary["deliveries"] += 1

    # 7. Janitor once per day
    last_janitor = state.get("janitor_last_run_at")
    j_age = timeutil.age_seconds(last_janitor)
    if last_janitor is None or (j_age is not None and j_age >= 86400):
        from foreman import janitor
        janitor.janitor_pass(state)
        state["janitor_last_run_at"] = now_iso

    # 8. Weekly lessons reminder
    lessons_state = state.setdefault("lessons", {})
    for p in all_projs:
        slug = p["slug"]
        lpath = paths.lessons_file(slug)
        if os.path.exists(lpath) and os.path.getsize(lpath) > 0:
            p_lessons = lessons_state.setdefault(slug, {})
            last_chk = p_lessons.get("last_checked_at")
            chk_age = timeutil.age_seconds(last_chk)
            if last_chk is None or (chk_age is not None and chk_age >= 7 * 86400):
                try:
                    with open(lpath, "rb") as f:
                        cur_hash = hashlib.sha256(f.read()).hexdigest()
                    if cur_hash != p_lessons.get("last_hash"):
                        wake.wake(slug, "propose project-rule changes via PR")
                        summary["wakes"] += 1
                        p_lessons["last_hash"] = cur_hash
                    p_lessons["last_checked_at"] = now_iso
                except OSError:
                    pass

    # Save state
    state["dedupe"] = sorted(list(dedupe))
    store.write_json(paths.watch_state_file(), state)

    return summary
