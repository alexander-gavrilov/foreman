"""Session context size and last-message excerpts from transcripts (architecture 4)."""
import json
import os

TAIL_BYTES = 4 * 1024 * 1024
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _tail_rows(path, limit=TAIL_BYTES):
    """Parsed JSON rows from the last `limit` bytes of a JSONL file, newest last. A line cut by the tail
    boundary is dropped. [] when the file is missing or unreadable."""
    if not path:
        return []
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            start = max(0, size - limit)
            f.seek(start)
            data = f.read()
    except OSError:
        return []
    if start > 0:
        nl = data.find(b"\n")
        data = b"" if nl < 0 else data[nl + 1:]
    rows = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _main_chain_assistant(row):
    if row.get("isSidechain") or row.get("type") != "assistant":
        return None
    msg = row.get("message")
    if not isinstance(msg, dict) or msg.get("model") == "<synthetic>":
        return None
    return msg


def claude_context_tokens(path):
    """Context size of a Claude Code session: input + cache_creation + cache_read + output tokens of the last
    main-chain assistant message that carries usage. None = unknown."""
    for row in reversed(_tail_rows(path)):
        msg = _main_chain_assistant(row)
        usage = msg.get("usage") if msg else None
        if isinstance(usage, dict) and any(isinstance(usage.get(k), (int, float)) for k in USAGE_KEYS):
            return int(sum(usage.get(k) or 0 for k in USAGE_KEYS if isinstance(usage.get(k), (int, float))))
    return None


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"
                 and isinstance(b.get("text"), str)]
        return "\n".join(parts)
    return ""


def last_assistant_text(path):
    """Best-effort text of the last assistant message in a transcript (Claude layout, Antigravity
    `source: MODEL` / `type: PLANNER_RESPONSE`, else a generic `role: assistant|model` layout).
    None when nothing is found."""
    for row in reversed(_tail_rows(path)):
        if row.get("type") == "USER_INPUT":
            return None  # agy: the turn's start; an older answer must not pass as this turn's
        text = None
        msg = _main_chain_assistant(row)
        if msg is not None:
            text = _content_text(msg.get("content"))
        elif row.get("source") == "MODEL" and row.get("type") == "PLANNER_RESPONSE":
            text = _content_text(row.get("content"))
        elif row.get("role") in ("assistant", "model") and not row.get("isSidechain"):
            text = _content_text(row.get("content"))
        if text and text.strip():
            return " ".join(text.split())
    return None


def excerpt(text, limit=200):
    """Whitespace-collapsed text cut to `limit` characters (ellipsis included)."""
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit - 1] + "…"
