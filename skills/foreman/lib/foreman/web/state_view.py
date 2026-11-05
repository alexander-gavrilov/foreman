"""State view generator for web API (web-api.md).

Builds:
- /api/state: version, machine, limits, thresholds, projects (with triage group & dot), findings_count.
- /api/projects/<slug>: strip, chat, board, sessions (with state_age & context_level), watches.
"""
import os

from foreman import config, limits as limits_mod, paths, projects, sessions, store, timeutil

GROUP_PRIORITY = {
    "needs_you": 0,
    "stuck": 1,
    "working": 2,
    "quiet": 3,
}

GROUP_DOTS = {
    "needs_you": "accent",
    "stuck": "red",
    "working": "green",
    "quiet": "grey",
}


def _format_age(observed_at, ref_now=None):
    if not observed_at:
        return "unknown"
    age = timeutil.age_text(observed_at, now=ref_now)
    return age if age != "?" else "unknown"


def _compute_level(used_pct, warn_th=70, stop_th=90):
    if used_pct is None:
        return "unknown"
    if used_pct >= stop_th:
        return "stop"
    if used_pct >= warn_th:
        return "warn"
    return "ok"


def _overall_level(window_levels):
    if "stop" in window_levels:
        return "stop"
    if "warn" in window_levels:
        return "warn"
    if "ok" in window_levels:
        return "ok"
    return "unknown"


def _build_limits(cfg):
    """Build limits dict matching web-api.md. Reads limits module or limits/*.json."""
    warn_th = cfg.get("thresholds", {}).get("limit_warn", 70)
    stop_th = cfg.get("thresholds", {}).get("limit_stop", 90)
    ref_now = timeutil.now()

    providers = {
        "claude": {
            "label": "Claude",
            "default_windows": [
                {"key": "weekly", "label": "неделя"},
                {"key": "five_hour", "label": "5 ч"},
            ],
        },
        "antigravity": {
            "label": "Antigravity",
            "default_windows": [
                {"key": "weekly", "label": "неделя"},
                {"key": "five_hour", "label": "5 ч"},
                {"key": "3p_five_hour", "label": "3p 5 ч"},
            ],
        },
    }

    out = {}
    for prov, meta in providers.items():
        raw_windows = limits_mod.load_provider_limits(prov)  # {window: {used_pct, resets_at, observed_at}}

        windows_list = []
        for def_win in meta["default_windows"]:
            k = def_win["key"]
            rw = raw_windows.get(k) or (raw_windows.get("seven_day") if k == "weekly" else None) or {}
            used_pct = rw.get("used_pct")
            resets_at = rw.get("resets_at")
            observed_at = rw.get("observed_at")
            level = _compute_level(used_pct, warn_th, stop_th)
            age = _format_age(observed_at, ref_now)
            windows_list.append({
                "key": k,
                "label": def_win["label"],
                "used_pct": used_pct,
                "resets_at": resets_at,
                "observed_at": observed_at,
                "age": age,
                "level": level,
            })

        levels = [w["level"] for w in windows_list]
        overall = _overall_level(levels)
        out[prov] = {
            "label": meta["label"],
            "level": overall,
            "windows": windows_list,
        }

    return out


def _get_active_stage(slug):
    """Current stage from active tasks on the board."""
    tasks = projects.list_tasks(slug)
    for t in tasks:
        if t.get("status") != "done":
            return t.get("stage") or t.get("title") or ""
    return ""


def _triage_project(proj, proj_sessions, chat_messages):
    """Determine (group, dot, line, unanswered_count, updated_at)."""
    slug = proj["slug"]

    # 1. Check unanswered questions
    unanswered_questions = [m for m in chat_messages if m.get("question") and not m.get("answered_by")]
    unanswered_count = len(unanswered_questions)

    # 2. Check sessions state
    # closed and rotated-out sessions are history: a dead terminal there must not strand the project in "stuck"
    has_stuck_or_gone = any(s.get("state") in ("stuck", "gone") and s.get("lifecycle") not in ("closed", "rotated")
                            for s in proj_sessions)
    has_busy = any(s.get("state") in ("busy", "starting") for s in proj_sessions
                   if sessions.is_live(s))

    # 3. Check live orchestrator
    orch_sid = proj.get("orchestrator")
    orch_session = None
    if orch_sid:
        orch_session = sessions.find(orch_sid, project=slug)
    if not orch_session:
        candidates = [s for s in proj_sessions if s.get("role") == "orchestrator" and not sessions.is_stray(s)]
        orch_session = sessions._prefer_live(candidates)

    orch_is_live = (orch_session is not None
                    and sessions.is_live(orch_session)
                    and orch_session.get("state") != "stuck")

    # Group determination
    if unanswered_count > 0:
        group = "needs_you"
        line = unanswered_questions[-1].get("text", "")
    elif has_stuck_or_gone or not orch_is_live:
        group = "stuck"
        line = _get_active_stage(slug)
    elif has_busy:
        group = "working"
        line = _get_active_stage(slug)
    else:
        group = "quiet"
        line = _get_active_stage(slug)

    dot = GROUP_DOTS.get(group, "grey")

    # updated_at calculation
    timestamps = [proj.get("created_at", "")]
    if chat_messages:
        timestamps.append(chat_messages[-1].get("ts", ""))
    tasks = projects.list_tasks(slug)
    if tasks:
        timestamps.append(max(t.get("updated_at", "") for t in tasks))
    for s in proj_sessions:
        for f in ("state_since", "last_event_at", "opened_at"):
            val = s.get(f)
            if val:
                timestamps.append(val)

    valid_ts = [ts for ts in timestamps if ts]
    updated_at = max(valid_ts) if valid_ts else timeutil.now_iso()

    return group, dot, line, unanswered_count, updated_at


def build_state(version=1):
    """Build the state response for GET /api/state."""
    cfg = config.load()

    # Findings count
    f_dir = paths.findings_dir()
    findings_count = 0
    try:
        findings_count = len([f for f in os.listdir(f_dir) if f.endswith(".json")])
    except OSError:
        findings_count = 0

    thresholds = {
        "limit_warn": cfg.get("thresholds", {}).get("limit_warn", 70),
        "limit_stop": cfg.get("thresholds", {}).get("limit_stop", 90),
        "context_soft": cfg.get("thresholds", {}).get("context_soft", 150000),
    }

    all_projs = projects.list_projects()
    proj_items = []
    for p in all_projs:
        slug = p["slug"]
        proj_sessions = sessions.list_sessions(project=slug)
        chat_msgs = projects.chat_list(slug)
        group, dot, line, unanswered, updated_at = _triage_project(p, proj_sessions, chat_msgs)
        proj_items.append({
            "slug": slug,
            "name": p.get("name") or slug,
            "group": group,
            "line": line,
            "dot": dot,
            "updated_at": updated_at,
            "unanswered": unanswered,
        })

    # Sort projects by priority group, then by updated_at descending
    proj_items.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
    proj_items.sort(key=lambda item: GROUP_PRIORITY.get(item["group"], 99))

    return {
        "version": version,
        "machine": {
            "name": cfg.get("machine", {}).get("name", "machine"),
            "kind": cfg.get("machine", {}).get("kind", "home"),
        },
        "limits": _build_limits(cfg),
        "thresholds": thresholds,
        "projects": proj_items,
        "findings_count": findings_count,
    }


def _context_level(tokens, context_soft=150000):
    if tokens is None:
        return "unknown"
    return "warn" if tokens >= context_soft else "ok"


def build_project(slug):
    """Build response for GET /api/projects/<slug>."""
    proj = projects.load_project(slug)
    if not proj:
        return None

    cfg = config.load()
    context_soft = cfg.get("thresholds", {}).get("context_soft", 150000)
    ref_now = timeutil.now()

    proj_sessions = sessions.list_sessions(project=slug)
    board = projects.load_board(slug)
    chat = projects.chat_list(slug, limit=200)

    # Format chat: last 200 messages, oldest first.
    # Only question: true messages carry options.
    formatted_chat = []
    for m in chat:
        item = {
            "id": m.get("id"),
            "ts": m.get("ts"),
            "from": m.get("from"),
            "channel": m.get("channel"),
            "text": m.get("text"),
            "question": bool(m.get("question")),
            "options": list(m.get("options") or []) if m.get("question") else [],
            "answered_by": m.get("answered_by"),
        }
        formatted_chat.append(item)

    # Format sessions: state_age, context_level
    formatted_sessions = []
    heaviest = None
    max_tokens = -1
    live_count = 0

    for s in proj_sessions:
        tokens = s.get("context_tokens")
        lvl = _context_level(tokens, context_soft)
        age = _format_age(s.get("state_since"), ref_now)
        is_live = sessions.is_live(s)
        if is_live:
            live_count += 1

        lifecycle = s.get("lifecycle")
        if not lifecycle:
            lifecycle = "active"

        formatted_sessions.append({
            "sid": s.get("sid"),
            "title": s.get("title") or s.get("sid"),
            "agent": s.get("agent"),
            "model": s.get("model"),
            "role": s.get("role"),
            "owner": s.get("owner"),
            "state": s.get("state"),
            "lifecycle": lifecycle,
            "state_age": age,
            "context_tokens": tokens,
            "context_level": lvl,
            "replaces": s.get("replaces"),
            "replaced_by": s.get("replaced_by"),
        })

        if is_live and tokens is not None and tokens > max_tokens:
            max_tokens = tokens
            heaviest = {
                "title": s.get("title") or s.get("sid"),
                "context_tokens": tokens,
                "level": lvl,
            }

    # If no tokens known, heaviest can still show a live session with unknown level
    live_formatted = [f for f, s in zip(formatted_sessions, proj_sessions) if sessions.is_live(s)]
    if heaviest is None and live_formatted:
        first = live_formatted[0]
        heaviest = {
            "title": first["title"],
            "context_tokens": first["context_tokens"],
            "level": first["context_level"],
        }

    active_stage = _get_active_stage(slug)
    strip = {
        "stage": active_stage,
        "sessions": live_count,
        "heaviest": heaviest,
    }

    # Watches
    hb = store.read_json(paths.watch_heartbeat_file(), None)
    if isinstance(hb, dict) and hb.get("last_run_at"):
        age_str = _format_age(hb["last_run_at"], ref_now)
        watch_status = "running, last pass %s ago" % age_str
    else:
        watch_status = "not running"

    watches = [
        {"what": "watch loop", "status": watch_status}
    ]

    return {
        "slug": slug,
        "name": proj.get("name") or slug,
        "path": proj.get("path", ""),
        "strip": strip,
        "chat": formatted_chat,
        "board": board,
        "sessions": formatted_sessions,
        "watches": watches,
    }
