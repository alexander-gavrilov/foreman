"""Atomic JSON / JSONL storage (architecture §2): every write goes through tmp + os.replace under an
fcntl.flock on <file>.lock; JSONL appends take the same lock."""
import contextlib
import fcntl
import json
import os
import re
import tempfile


@contextlib.contextmanager
def locked(path):
    """Exclusive advisory lock on <path>.lock (created on demand, parent dirs too)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd = os.open(path + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data, mode=None):
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    """Parsed JSON, or `default` when the file is missing or unreadable."""
    return _read_json(path, default)


def write_json(path, data, mode=None):
    with locked(path):
        _write_json(path, data, mode)


def update_json(path, fn, default=None, mode=None):
    """Read-modify-write under one lock: fn(current_or_default) -> new value (also returned)."""
    with locked(path):
        new = fn(_read_json(path, default))
        _write_json(path, new, mode)
        return new


def _parse_lines(text):
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def append_jsonl(path, obj):
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with locked(path):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    return obj


def read_jsonl(path):
    """All parseable rows (corrupt lines are skipped); [] when the file does not exist."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _parse_lines(f.read())
    except OSError:
        return []


def tail_jsonl(path, n):
    if n <= 0:
        return []
    return read_jsonl(path)[-n:]


def search_jsonl(path, pattern, field=None, limit=None):
    """Rows whose `field` (or whole JSON line when field is None) matches `pattern`
    (case-insensitive regex; an invalid regex is treated as a plain substring).
    Chronological order; with `limit`, only the newest `limit` matches."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
        match = lambda s: rx.search(s) is not None
    except re.error:
        low = pattern.lower()
        match = lambda s: low in s.lower()
    hits = []
    for row in read_jsonl(path):
        hay = row.get(field) if (field and isinstance(row, dict)) else json.dumps(row, ensure_ascii=False)
        if isinstance(hay, str) and match(hay):
            hits.append(row)
    if limit is not None and limit >= 0:
        hits = hits[-limit:] if limit else []
    return hits


def rewrite_jsonl(path, fn):
    """Replace the file with fn(rows) -> rows, atomically, under the lock."""
    with locked(path):
        rows = fn(read_jsonl(path))
        directory = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return rows
