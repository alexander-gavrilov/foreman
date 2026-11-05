"""Provider limits and context window tracking (architecture §6, spec §4.3, §4.4)."""
from datetime import datetime, timezone
import json
import os

from foreman import config, paths, sessions, store, timeutil

_PROVIDERS = ("claude", "antigravity")


def _to_iso(ts):
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts)


def record_reading(provider, windows, observed_at=None):
    """Merge new window readings into stored readings for provider.

    Never overwrites a window with a missing/null/empty value (keeps last good + observed_at).
    """
    path = paths.limits_file(provider)
    obs = observed_at or timeutil.now_iso()

    def apply(current):
        if not isinstance(current, dict):
            current = {}
        for name, data in (windows or {}).items():
            if not isinstance(data, dict):
                continue
            used = data.get("used_pct")
            if used is None or used == "":
                continue
            try:
                used_pct = int(round(float(used)))
            except (ValueError, TypeError):
                continue
            resets_at = _to_iso(data.get("resets_at"))
            current[name] = {
                "used_pct": used_pct,
                "resets_at": resets_at,
                "observed_at": obs,
            }
        return current

    return store.update_json(path, apply, default={})


def parse_claude_statusline(payload):
    """Parse Claude statusline JSON for rate limits (five_hour, seven_day)."""
    if not isinstance(payload, dict):
        return {}
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return {}

    windows = {}
    for key in ("five_hour", "seven_day"):
        entry = rate_limits.get(key)
        if isinstance(entry, dict) and entry.get("used_percentage") is not None:
            try:
                used_pct = int(round(float(entry["used_percentage"])))
                resets_at = _to_iso(entry.get("resets_at"))
                windows[key] = {"used_pct": used_pct, "resets_at": resets_at}
            except (ValueError, TypeError):
                continue
    return windows


def parse_antigravity_statusline(payload):
    """Parse Antigravity statusline JSON for quotas.

    Maps gemini-5h->five_hour, gemini-weekly->weekly, 3p-5h->3p_five_hour, 3p-weekly->3p_weekly.
    quota null -> nothing recorded (returns empty dict).
    """
    if not isinstance(payload, dict):
        return {}
    quota = payload.get("quota")
    if not isinstance(quota, dict):
        return {}

    mapping = {
        "gemini-5h": "five_hour",
        "gemini-weekly": "weekly",
        "3p-5h": "3p_five_hour",
        "3p-weekly": "3p_weekly",
    }
    windows = {}
    for raw_k, target_k in mapping.items():
        entry = quota.get(raw_k)
        if isinstance(entry, dict) and entry.get("remaining_fraction") is not None:
            try:
                rem = float(entry["remaining_fraction"])
                used_pct = int(round(100.0 * (1.0 - rem)))
                resets_at = _to_iso(entry.get("reset_time"))
                windows[target_k] = {"used_pct": used_pct, "resets_at": resets_at}
            except (ValueError, TypeError):
                continue
    return windows


def _newest_observed(windows):
    newest = None
    for w in windows.values():
        if isinstance(w, dict) and w.get("observed_at"):
            ts = timeutil.parse(w["observed_at"])
            if ts and (newest is None or ts > newest):
                newest = ts
    return newest


def import_legacy_snapshots():
    """Import ~/.cache/agent-limits/claude.json and agy.json when newer than stored reading."""
    cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "agent-limits")

    # Claude
    claude_path = os.path.join(cache_dir, "claude.json")
    if os.path.exists(claude_path):
        try:
            with open(claude_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                at = data.get("at")
                file_dt = datetime.fromtimestamp(at, timezone.utc) if at else None
                cur = store.read_json(paths.limits_file("claude"), default={})
                cur_dt = _newest_observed(cur)
                if file_dt and (cur_dt is None or file_dt > cur_dt):
                    windows = parse_claude_statusline(data)
                    if windows:
                        record_reading("claude", windows, observed_at=_to_iso(at))
        except Exception:
            pass

    # Antigravity
    agy_path = os.path.join(cache_dir, "agy.json")
    if os.path.exists(agy_path):
        try:
            with open(agy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("quota") is not None:
                at = data.get("at")
                if at:
                    file_dt = datetime.fromtimestamp(at, timezone.utc)
                else:
                    file_dt = datetime.fromtimestamp(os.path.getmtime(agy_path), timezone.utc)
                cur = store.read_json(paths.limits_file("antigravity"), default={})
                cur_dt = _newest_observed(cur)
                if file_dt and (cur_dt is None or file_dt > cur_dt):
                    windows = parse_antigravity_statusline(data)
                    if windows:
                        record_reading("antigravity", windows, observed_at=_to_iso(file_dt.timestamp()))
        except Exception:
            pass


def load_provider_limits(provider):
    """Load windows for provider, checking legacy snapshots first."""
    import_legacy_snapshots()
    data = store.read_json(paths.limits_file(provider), default={})
    return data if isinstance(data, dict) else {}


def _window_level(used_pct, limit_warn, limit_stop):
    if used_pct is None:
        return "unknown"
    if used_pct >= limit_stop:
        return "stop"
    if used_pct >= limit_warn:
        return "warn"
    return "ok"


def levels():
    """Return {provider: "ok"|"warn"|"stop"|"unknown"} for claude and antigravity."""
    limit_warn = config.get("thresholds.limit_warn", 70)
    limit_stop = config.get("thresholds.limit_stop", 90)

    rank = {"unknown": 0, "ok": 1, "warn": 2, "stop": 3}
    out = {}
    for prov in _PROVIDERS:
        windows = load_provider_limits(prov)
        valid = [w["used_pct"] for w in windows.values() if isinstance(w, dict) and w.get("used_pct") is not None]
        if not valid:
            out[prov] = "unknown"
        else:
            win_levels = [_window_level(pct, limit_warn, limit_stop) for pct in valid]
            out[prov] = max(win_levels, key=lambda l: rank.get(l, 0))

    if os.path.exists(paths.limits_file("codex")):
        windows = load_provider_limits("codex")
        valid = [w["used_pct"] for w in windows.values() if isinstance(w, dict) and w.get("used_pct") is not None]
        if not valid:
            out["codex"] = "unknown"
        else:
            win_levels = [_window_level(pct, limit_warn, limit_stop) for pct in valid]
            out["codex"] = max(win_levels, key=lambda l: rank.get(l, 0))

    return out


def summary_line():
    """One-line limits summary for status reports (weekly first; unknown shown as '?')."""
    parts = []

    # Claude
    cl = load_provider_limits("claude")
    wk_entry = cl.get("seven_day") or cl.get("weekly")
    fh_entry = cl.get("five_hour")
    if wk_entry or fh_entry:
        items = []
        if wk_entry and wk_entry.get("used_pct") is not None:
            items.append("wk %d%%" % wk_entry["used_pct"])
        else:
            items.append("wk ?")
        if fh_entry and fh_entry.get("used_pct") is not None:
            items.append("5h %d%%" % fh_entry["used_pct"])
        else:
            items.append("5h ?")
        newest = _newest_observed(cl)
        age = timeutil.age_text(_to_iso(newest.timestamp())) if newest else "?"
        parts.append("Claude " + " · ".join(items) + " (%s)" % age)
    else:
        parts.append("Claude ?")

    # Antigravity
    agy = load_provider_limits("antigravity")
    wk_entry = agy.get("weekly") or agy.get("seven_day")
    fh_entry = agy.get("five_hour")
    p3_entry = agy.get("3p_five_hour")
    if wk_entry or fh_entry:
        items = []
        if wk_entry and wk_entry.get("used_pct") is not None:
            items.append("wk %d%%" % wk_entry["used_pct"])
        else:
            items.append("wk ?")
        if fh_entry and fh_entry.get("used_pct") is not None:
            items.append("5h %d%%" % fh_entry["used_pct"])
        else:
            items.append("5h ?")
        if p3_entry and p3_entry.get("used_pct") is not None:
            items.append("3p 5h %d%%" % p3_entry["used_pct"])
        newest = _newest_observed(agy)
        age = timeutil.age_text(_to_iso(newest.timestamp())) if newest else "?"
        parts.append("agy " + " · ".join(items) + " (%s)" % age)
    else:
        parts.append("agy ?")

    return " | ".join(parts)


def store_session_context(agent, payload):
    """Store context from statusline context_window on matched session."""
    if not isinstance(payload, dict):
        return
    ctx = payload.get("context_window")
    if not isinstance(ctx, dict):
        return

    tokens = None
    cur = ctx.get("current_usage")
    if isinstance(cur, dict):
        total = 0
        has_val = False
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            v = cur.get(k)
            if isinstance(v, (int, float)):
                total += int(v)
                has_val = True
        if has_val:
            tokens = total

    if tokens is None:
        total_in = ctx.get("total_input_tokens")
        if isinstance(total_in, (int, float)):
            tokens = int(total_in)

    if tokens is None:
        return

    sess = None
    if agent == "claude":
        session_id = payload.get("session_id")
        if session_id:
            sess = sessions.find_by_agent_session_id(session_id) or sessions.find(session_id)
    elif agent in ("antigravity", "agy"):
        handle = os.environ.get("ORCA_TERMINAL_HANDLE")
        if handle:
            sess = sessions.find_by_handle(handle)
        if not sess:
            conv_id = payload.get("conversationId") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
            if conv_id:
                sess = sessions.find_by_agent_session_id(conv_id) or sessions.find(conv_id)

    if sess and sess.get("sid"):
        sessions.update_session(sess["sid"], context_tokens=tokens, context_at=timeutil.now_iso())
