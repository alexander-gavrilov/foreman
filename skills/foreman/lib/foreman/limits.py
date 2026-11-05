"""Provider limits (owner: WP3). Interface stub; WP3 replaces the body, keeps the signatures."""


def levels():
    """Return {provider: "ok"|"warn"|"stop"|"unknown"} for "claude" and "antigravity" (and "codex" if known)."""
    return {"claude": "unknown", "antigravity": "unknown"}


def summary_line():
    """One-line limits summary for status reports, e.g. 'Claude wk 61% · 5h 42% (4m) | agy wk 33% · 5h 10% (2h)'."""
    return "limits: unknown"
