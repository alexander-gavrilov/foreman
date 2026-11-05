"""Model routing (spec §4.4, §4.5): which agent, model and effort a new session gets.

`choose(kind, levels, preferred=None)` returns (agent, model, effort, note). `levels` is limits.levels(),
{provider: "ok"|"warn"|"stop"|"unknown"} (or per-provider/window reading dicts). The tables below are only defaults:
config.json `routing.<kind>` (a list of {"agent", "model", "effort"} objects or [agent, model, effort] rows, in order
of preference) replaces them."""
from datetime import datetime, timezone

from foreman import config, timeutil

COLD_CACHE_AFTER_S = 55 * 60
_RANK = {"unknown": 0, "ok": 1, "warn": 2, "stop": 3}

# (agent, model, effort), best first. Antigravity entries may serve Claude models; they still count against
# the antigravity provider.
DEFAULT_ROUTING = {
    "routine": [("claude", "sonnet", None), ("antigravity", "gemini-3.8-flash-high", None)],
    "research": [("antigravity", "gemini-3.8-flash-medium", None), ("claude", "sonnet", None)],
    "design": [("antigravity", "gemini-4-argon-medium", None)],
    "review": [("claude", "opus", None), ("antigravity", "claude-opus-5-5-high", None)],
    "hard": [("claude", "opus", None), ("antigravity", "claude-opus-5-5-high", None)],
    "orchestrator": [("claude", "sonnet", None)],
}
KINDS = tuple(DEFAULT_ROUTING)
ORCHESTRATOR_AGENTS = ("claude", "antigravity")  # decision D23: codex is worker-only in v1


class RoutingError(Exception):
    """No acceptable agent/model. `providers` lists the providers refused because of their limits."""

    def __init__(self, message, providers=None):
        super().__init__(message)
        self.providers = list(providers or [])


def provider_of(agent, model=None):
    """The subscription a session spends. Claude models served through Antigravity count as antigravity."""
    return agent


def _parse_reset(val):
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    return timeutil.parse(val)


def window_level(window, now=None):
    """Level of one limit window dict ({used_pct, resets_at, observed_at, level?}).
    A reading whose window reset time has already passed counts as 'unknown' (architecture §6)."""
    if not isinstance(window, dict):
        return "unknown"
    reset_dt = _parse_reset(window.get("resets_at"))
    if reset_dt is not None:
        ref = now if now is not None else timeutil.now()
        if ref >= reset_dt:
            return "unknown"
    pct = window.get("used_pct")
    if pct is None:
        pct = window.get("used_percentage")
    if isinstance(pct, (int, float)) and not isinstance(pct, bool):
        stop = config.get("thresholds.limit_stop")
        warn = config.get("thresholds.limit_warn")
        stop = 90 if stop is None else stop
        warn = 70 if warn is None else warn
        if pct >= stop:
            return "stop"
        if pct >= warn:
            return "warn"
        return "ok"
    lvl = window.get("level")
    return lvl if lvl in _RANK else "unknown"


def provider_level(value, now=None):
    """Effective level ('ok'|'warn'|'stop'|'unknown') from a level string or a provider/window reading dict."""
    if isinstance(value, str):
        return value if value in _RANK else "unknown"
    if not isinstance(value, dict):
        return "unknown"
    windows = value.get("windows")
    if isinstance(windows, dict):
        windows = list(windows.values())
    elif not isinstance(windows, list):
        sub = [v for v in value.values() if isinstance(v, dict)]
        windows = sub if sub else None
    if windows:
        levels = [window_level(w, now=now) for w in windows if isinstance(w, dict)]
        return max(levels, key=lambda k: _RANK.get(k, 0)) if levels else "unknown"
    return window_level(value, now=now)


def _entry(row):
    if isinstance(row, dict):
        agent, model, effort = row.get("agent"), row.get("model"), row.get("effort")
    elif isinstance(row, (list, tuple)) and 1 <= len(row) <= 3:
        agent, model, effort = (list(row) + [None, None])[:3]
    else:
        return None
    if not isinstance(agent, str) or not agent:
        return None
    return (agent, model or None, effort or None)


def table(kind):
    """Candidate (agent, model, effort) list for a kind: config override, else the built-in default."""
    rows = (config.get("routing." + kind) or []) if isinstance(kind, str) else []
    entries = [e for e in (_entry(r) for r in rows if isinstance(rows, list)) if e]
    if entries:
        return entries
    if kind not in DEFAULT_ROUTING:
        raise RoutingError("unknown session kind %r (known: %s)" % (kind, ", ".join(KINDS)))
    return list(DEFAULT_ROUTING[kind])


def _explicit(preferred):
    if isinstance(preferred, str):
        return (preferred, None, None)
    if isinstance(preferred, dict):
        return (preferred.get("agent"), preferred.get("model"), preferred.get("effort"))
    agent, model, effort = (list(preferred) + [None, None])[:3]
    return (agent, model, effort)


def _level(levels, provider):
    return provider_level((levels or {}).get(provider))


def choose(kind, levels, preferred=None):
    candidates = table(kind)
    if preferred:
        agent, model, effort = _explicit(preferred)
        if not agent:
            raise RoutingError("no agent named")
        if not model:  # an agent named without a model: the model this kind uses on that agent, else its default
            match = next((c for c in candidates if c[0] == agent), None)
            model = match[1] if match else None
            effort = effort or (match[2] if match else None)
        provider = provider_of(agent, model)
        level = _level(levels, provider)
        if level == "stop":
            raise RoutingError("%s is at its limit (stop): no new sessions on it" % provider, [provider])
        return agent, model, effort, _note_for(provider, level)

    usable = []
    stopped = []
    for cand in candidates:
        provider = provider_of(cand[0], cand[1])
        if _level(levels, provider) == "stop":
            if provider not in stopped:
                stopped.append(provider)
        else:
            usable.append(cand)
    if not usable:
        raise RoutingError("no provider available for %s: %s at its limit (stop)" % (kind, ", ".join(stopped)),
                           stopped)
    calm = [c for c in usable if _level(levels, provider_of(c[0], c[1])) != "warn"]
    pick = calm[0] if calm else usable[0]
    notes = []
    top = usable[0]
    top_provider = provider_of(top[0], top[1])
    if pick is not top and _level(levels, top_provider) == "warn":
        notes.append("%s is at warn: preferring %s" % (top_provider, provider_of(pick[0], pick[1])))
    else:
        note = _note_for(provider_of(pick[0], pick[1]), _level(levels, provider_of(pick[0], pick[1])))
        if note:
            notes.append(note)
    if stopped:
        notes.append("%s at stop: skipped" % ", ".join(stopped))
    return pick[0], pick[1], pick[2], "; ".join(notes)


def _note_for(provider, level):
    if level == "warn":
        return "%s is at warn (limit 70%%+): no better provider for this" % provider
    if level == "unknown":
        return "%s limits unknown: going ahead" % provider
    return ""


def cold_cache_warning(session, now=None):
    """Text warning for `foreman ask` to a rotated session whose prompt cache is probably cold (its last event is
    older than 55 minutes), else None."""
    if not isinstance(session, dict):
        return None
    if session.get("lifecycle") != "rotated":
        return None
    stamp = session.get("last_event_at") or session.get("state_since")
    age = timeutil.age_seconds(stamp, now=now)
    if age is None or age <= COLD_CACHE_AFTER_S:
        return None
    return ("warning: %s was rotated and its last activity was %s ago: its prompt cache is cold, so this "
            "question costs a full context re-read. Prefer the handoff or its transcript."
            % (session.get("sid") or "session", timeutil.humanize(age)))
