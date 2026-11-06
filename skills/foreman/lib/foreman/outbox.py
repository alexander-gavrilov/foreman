"""D60: durable outbox for the Telegram topic mirror.

`messaging` appends a row and returns at once (N9: an unreachable Telegram never delays a reply). A drainer sends
the rows in order: `foreman serve` runs one on its own thread every ~2 s; a CLI process without a running daemon
makes one bounded attempt at exit. Only one drainer works at a time (non-blocking flock). A row is removed only
after the Bot API said ok; a failure is logged (API description, never the token) and retried with backoff
(`retry_after` of a 429 honoured); a row older than one hour is dropped with an event. Order is kept per project;
the per-project rate limit is decided once per row and stored in it, so a retry does not consume another slot and
the "more messages in the web chat" pointer survives retries."""
import contextlib
import fcntl
import os
import threading
import time
import uuid

from foreman import paths, store, timeutil

MAX_AGE_S = 3600
BACKOFF_S = (1, 2, 5, 15, 30, 60)  # after the n-th failed attempt; the last value repeats
DRAIN_INTERVAL_S = 2.0
EXIT_BUDGET_S = 3.0  # a CLI process waits at most this long at exit; whatever is left stays for the daemon
DAEMON_FRESH_S = 15.0
HEARTBEAT_EVERY_S = 5.0
_now = time.time


def path():
    return os.path.join(paths.home(), "telegram-outbox.jsonl")


def heartbeat_path():
    return os.path.join(paths.home(), "telegram-outbox-drainer.json")


def pending():
    return store.read_jsonl(path())


def enqueue(project, text, msg_id=None):
    row = {"id": "o-" + uuid.uuid4().hex[:10], "project": project, "text": text, "msg_id": msg_id,
           "created_at": timeutil.now_iso(), "created_ts": _now(), "attempts": 0, "next_at": 0}
    store.append_jsonl(path(), row)
    return row


def _log(kind, **fields):
    try:
        from foreman import hooks
        hooks._append_event(dict({"kind": kind}, **fields))
    except Exception:
        pass


def _clean(text):
    """The text of an error with the bot token removed."""
    text = str(text)
    try:
        from foreman import telegram
        token = telegram.get_token()
        if token:
            text = text.replace(token, "<token>")
    except Exception:
        pass
    return text


@contextlib.contextmanager
def _drainer_lock():
    """Non-blocking exclusive lock: yields True if this process is the only drainer, False otherwise."""
    fd = os.open(path() + ".drain", os.O_RDWR | os.O_CREAT, 0o600)
    got = False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
        except OSError:
            pass
        yield got
    finally:
        try:
            if got:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _update(row_id, fields=None):
    """Replace fields of a row, or remove it (fields None), under the file lock."""
    def fn(rows):
        out = []
        for r in rows:
            if r.get("id") == row_id:
                if fields is None:
                    continue
                r = dict(r, **fields)
            out.append(r)
        return out
    store.rewrite_jsonl(path(), fn)


def _expire(now):
    rows = pending()
    for r in rows:
        age = now - r.get("created_ts", now)
        if age > MAX_AGE_S:
            _update(r["id"])
            _log("mirror_dropped", reason="older than 1 hour", project=r.get("project"), msg_id=r.get("msg_id"),
                 attempts=r.get("attempts", 0))
    return [r for r in rows if now - r.get("created_ts", now) <= MAX_AGE_S]


def _next_due(now):
    """-> (row, next_wake_ts). The first row of each project is its head; only a head whose backoff is over is due."""
    rows = _expire(now)
    heads, seen = [], set()
    for r in rows:
        if r.get("project") not in seen:
            seen.add(r.get("project"))
            heads.append(r)
    due = [r for r in heads if r.get("next_at", 0) <= now]
    if due:
        return due[0], None
    return None, (min(r.get("next_at", 0) for r in heads) if heads else None)


def _backoff(attempts, retry_after):
    wait = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        wait = max(wait, retry_after)
    return wait


def _attempt(row):
    from foreman import notify
    try:
        return notify.send_mirror_row(row)
    except Exception as exc:
        return {"ok": False, "description": "%s: %s" % (type(exc).__name__, exc)}


def drain(budget_s=None, wait=False):
    """Send due rows in order. `budget_s` bounds the time spent between sends; `wait` sleeps through backoffs
    until the budget is over. -> {"sent": n, "busy": bool}"""
    summary = {"sent": 0, "busy": False}
    if not os.path.exists(path()):
        return summary
    started = time.monotonic()
    with _drainer_lock() as got:
        if not got:
            summary["busy"] = True
            return summary
        while budget_s is None or time.monotonic() - started < budget_s:
            now = _now()
            row, wake_at = _next_due(now)
            if row is None:
                if wait and wake_at is not None and budget_s is not None:
                    time.sleep(max(0.0, min(wake_at - now, budget_s - (time.monotonic() - started), 1.0)))
                    continue
                break
            res = _attempt(row)
            if res.get("verdict") and res["verdict"] != row.get("verdict"):
                row = dict(row, verdict=res["verdict"])
                _update(row["id"], {"verdict": res["verdict"]})
            if res.get("ok"):
                _update(row["id"])
                summary["sent"] += 1
            elif res.get("drop"):
                _update(row["id"])
                _log("mirror_dropped", reason=_clean(res["drop"]), project=row.get("project"),
                     msg_id=row.get("msg_id"))
            else:
                attempts = row.get("attempts", 0) + 1
                delay = _backoff(attempts, res.get("retry_after"))
                _update(row["id"], {"attempts": attempts, "next_at": _now() + delay})
                _log("mirror_error", error=_clean(res.get("description") or "send failed"),
                     project=row.get("project"), msg_id=row.get("msg_id"), attempt=attempts, retry_in_s=delay)
    return summary


# ---------------------------------------------------------------- the daemon side and the exit attempt

def write_daemon_heartbeat():
    store.write_json(heartbeat_path(), {"pid": os.getpid(), "at": _now()})


def daemon_active():
    """True if a drain thread of a live process touched the heartbeat recently."""
    beat = store.read_json(heartbeat_path(), None)
    if not isinstance(beat, dict) or not isinstance(beat.get("at"), (int, float)):
        return False
    if _now() - beat["at"] > DAEMON_FRESH_S:
        return False
    pid = beat.get("pid")
    if not isinstance(pid, int):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass  # exists, but not ours
    return True


def start_drain_thread(stop_event, interval=None):
    """The daemon's drainer: one pass every `interval` seconds until `stop_event` is set."""
    interval = DRAIN_INTERVAL_S if interval is None else interval
    pinned = paths.home()  # the state root of the process that started the daemon

    def loop():
        beat_at = 0.0
        with paths.pinned_home(pinned):
            while not stop_event.is_set():
                try:
                    if time.monotonic() - beat_at >= HEARTBEAT_EVERY_S or not beat_at:
                        write_daemon_heartbeat()
                        beat_at = time.monotonic()
                    drain(budget_s=30)
                except Exception:
                    pass
                stop_event.wait(interval)
    t = threading.Thread(target=loop, name="foreman-outbox", daemon=True)
    t.start()
    return t


def exit_flush(budget_s=EXIT_BUDGET_S):
    """At exit of a short-lived CLI process: one bounded attempt, unless a daemon drains anyway. Whatever is not
    sent stays in the file."""
    try:
        if not os.path.exists(path()) or daemon_active():
            return
        home = paths.home()

        def work():
            with paths.pinned_home(home):
                drain(budget_s=budget_s)
        t = threading.Thread(target=work, name="foreman-outbox-exit", daemon=True)
        t.start()
        t.join(budget_s + 0.5)
    except Exception:
        pass
