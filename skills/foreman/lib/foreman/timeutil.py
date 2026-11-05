"""UTC time helpers. FOREMAN_NOW (ISO, Z) pins "now" for deterministic output in tests."""
import os
from datetime import datetime, timezone

_FMT = "%Y-%m-%dT%H:%M:%SZ"


def now():
    pinned = os.environ.get("FOREMAN_NOW")
    if pinned:
        parsed = parse(pinned)
        if parsed:
            return parsed
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_iso():
    return now().strftime(_FMT)


def parse(text):
    """ISO-8601 UTC ('...Z' or with offset) -> aware datetime; None when empty or invalid."""
    if not text or not isinstance(text, str):
        return None
    try:
        s = text.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def age_seconds(ts, now=None):
    d = parse(ts)
    if d is None:
        return None
    ref = now if now is not None else globals()["now"]()
    return int((ref - d).total_seconds())


def humanize(seconds):
    """45 -> '45s', 270 -> '4m', 3h10s -> '3h', 2d -> '2d'; None -> '?'."""
    if seconds is None:
        return "?"
    s = max(0, int(seconds))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    if s < 86400:
        return "%dh" % (s // 3600)
    return "%dd" % (s // 86400)


def age_text(ts, now=None):
    return humanize(age_seconds(ts, now=now))
