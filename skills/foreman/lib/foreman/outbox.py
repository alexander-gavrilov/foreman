"""D60: durable outbox for everything foreman sends to Telegram: the topic mirror of the project chat, and
notifications (a question with its answer buttons, --notify, system notices).

Callers append a row and return at once (N9: an unreachable Telegram never delays a reply, and a hook never waits).
A drainer sends the rows: `foreman serve`/`web` run one on a thread every ~2 s; a CLI process that queued a row and
sees no fresh daemon starts ONE detached, bounded `foreman outbox drain` and exits at once (`kick`; never from a
hook). Only one drainer works at a time (non-blocking flock; a drainer that finishes looks at the file again after
it let go of the lock, so a row queued while it was finishing is not stranded). Delivery is AT LEAST ONCE: a row
leaves the file only after the Bot API said ok, so a message Telegram accepted but whose answer we never saw (a read
timeout) is sent again; a rare duplicate is accepted, a lost message is not. `attempts`/`next_at` are written BEFORE
each send, so a killed drainer neither resends at once nor hides the failure (the next drainer logs it). A failure
is logged (API description, never the token) and retried with backoff (`retry_after` of a 429 honoured); a
permanent 4xx other than 429 drops the row at once with an event (a head row must never block its project); a row
older than one hour is dropped with an event. Order is kept per project; the per-project rate limit of mirror rows
is decided once per row and stored in it."""
import contextlib
import fcntl
import os
import subprocess
import sys
import threading
import time
import uuid

from foreman import paths, store, timeutil

MAX_AGE_S = 3600
BACKOFF_S = (1, 2, 5, 15, 30, 60)  # after the n-th failed attempt; the last value repeats
DRAIN_INTERVAL_S = 2.0
DETACHED_BUDGET_S = 120.0  # a detached drainer works at most this long, then leaves the rest to the next one
DAEMON_FRESH_S = 15.0
HEARTBEAT_EVERY_S = 5.0
SEND_WORST_S = 12.0  # topic creation + send, each bounded by notify.SEND_TIMEOUT_S: the least a send can take to fail
_now = time.time
_queued_here = False  # this process appended a row: its CLI entry point kicks a drainer before it exits


def path():
    return os.path.join(paths.home(), "telegram-outbox.jsonl")


def heartbeat_path():
    return os.path.join(paths.home(), "telegram-outbox-drainer.json")


def pending():
    return store.read_jsonl(path())


def enqueue(project, text, msg_id=None, kind="mirror", **extra):
    """Append a row. kind "mirror": the topic copy of a chat message (rate limited); "notify": a notification
    (`question`, `options` in `extra`), never rate limited."""
    global _queued_here
    row = dict({"id": "o-" + uuid.uuid4().hex[:10], "project": project, "text": text, "msg_id": msg_id, "kind": kind,
                "created_at": timeutil.now_iso(), "created_ts": _now(), "attempts": 0, "next_at": 0}, **extra)
    store.append_jsonl(path(), row)
    _queued_here = True
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


def _prepare(row):
    """Everything that must be on disk BEFORE the send starts: the attempt counter, a retry time no earlier than
    the longest a send can take, the rate-limit verdict. A drainer killed mid-send then cannot cause an immediate
    resend, and the failure shows up (the next drainer logs an unfinished attempt)."""
    from foreman import notify
    if row.get("inflight"):
        _log("mirror_error", error="the previous attempt did not finish (the drainer was killed)",
             project=row.get("project"), msg_id=row.get("msg_id"), attempt=row.get("attempts", 0))
    attempts = row.get("attempts", 0) + 1
    fields = {"attempts": attempts, "inflight": True,
              "next_at": _now() + max(_backoff(attempts, None), SEND_WORST_S)}
    try:
        fields["verdict"] = notify.row_verdict(row)
    except Exception:
        pass
    _update(row["id"], fields)
    return dict(row, **fields)


def drain(budget_s=None, wait=False):
    """Send due rows in order. `budget_s` bounds when a new send may start (a send that has started is finished);
    `wait` sleeps through backoffs that end within the budget. -> {"sent": n, "busy": bool}"""
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
                    left = budget_s - (time.monotonic() - started)
                    if wake_at - now > left:
                        break  # nothing becomes due within the budget
                    time.sleep(max(0.0, min(wake_at - now, left, 1.0)))
                    continue
                break
            row = _prepare(row)
            res = _attempt(row)
            if res.get("ok"):
                _update(row["id"])
                summary["sent"] += 1
            elif res.get("drop"):
                _update(row["id"])
                _log("mirror_dropped", reason=_clean(res["drop"]), project=row.get("project"),
                     msg_id=row.get("msg_id"), attempts=row["attempts"])
            else:
                attempts = row["attempts"]
                delay = _backoff(attempts, res.get("retry_after"))
                _update(row["id"], {"inflight": False, "next_at": _now() + delay})
                _log("mirror_error", error=_clean(res.get("description") or "send failed"),
                     project=row.get("project"), msg_id=row.get("msg_id"), attempt=attempts, retry_in_s=delay)
    return summary


def has_work(within_s=0.0):
    """True if a row is due now or becomes due within `within_s` seconds."""
    now = _now()
    rows = pending()
    heads, seen = [], set()
    for r in rows:
        if r.get("project") not in seen:
            seen.add(r.get("project"))
            heads.append(r)
    return any(r.get("next_at", 0) <= now + within_s for r in heads)


# ---------------------------------------------------------------- the daemon side and the detached drainer

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


def drainer_running():
    """True if another process holds the drainer lock right now."""
    try:
        fd = os.open(path() + ".drain", os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def drain_detached(budget_s=DETACHED_BUDGET_S):
    """The body of `foreman outbox drain`: work through the file, sleeping through backoffs, for at most
    `budget_s`. If another drainer holds the lock, leave at once: it looks at the file again after it lets go."""
    started = time.monotonic()
    while True:
        left = budget_s - (time.monotonic() - started)
        if left <= 0 or not os.path.exists(path()):
            return
        if drain(budget_s=left, wait=True).get("busy"):
            return
        left = budget_s - (time.monotonic() - started)
        if left <= 0 or not has_work(within_s=left):
            return


def _launcher_argv():
    here = os.path.dirname(os.path.abspath(__file__))
    launcher = os.path.normpath(os.path.join(here, os.pardir, os.pardir, "bin", "foreman"))
    if os.path.isfile(launcher):
        return [sys.executable, launcher]
    return [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from foreman.cli import main; "
            "sys.exit(main(sys.argv[1:]))" % os.path.dirname(here)]


def spawn_drainer():
    """Start a detached `foreman outbox drain` (its own session, no inherited streams) and return at once."""
    argv = _launcher_argv() + ["outbox", "drain"]
    try:
        os.makedirs(paths.home(), exist_ok=True)
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         close_fds=True, start_new_session=True, cwd=paths.home())
        return True
    except Exception as exc:
        _log("mirror_error", error="could not start the outbox drainer: %s: %s" % (type(exc).__name__, exc))
        return False


def kick():
    """Called once by the CLI entry point after a command that queued a row (never by a hook): unless a daemon or
    another drainer is on it, start a detached one. Costs a fork, never waits. $FOREMAN_OUTBOX_SPAWN=0 turns it
    off (tests; rows then wait for the daemon). -> True if one was started."""
    try:
        if os.environ.get("FOREMAN_OUTBOX_SPAWN") == "0":
            return False
        if not _queued_here or not os.path.exists(path()) or daemon_active() or drainer_running():
            return False
        return spawn_drainer()
    except Exception:
        return False
