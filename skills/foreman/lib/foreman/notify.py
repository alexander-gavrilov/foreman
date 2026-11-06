"""User notifications dispatcher (owner: WP6, architecture §10).
Dispatches notifications to configured channels (telegram, macos, file).
Failures never raise; they return a list of result dictionaries.
"""
import os
import time

from foreman import config, macnotify, paths, projects, store, telegram, timeutil


TG_LIMIT = 4000            # Telegram caps a message at 4096 characters
MIRROR_MAX_PER_WINDOW = 10  # chat messages mirrored to one topic per window; a flood is cut off with a pointer
MIRROR_WINDOW_S = 60
_now = time.time


def web_link(project, msg_id=None):
    """Token-free link to the project's web chat (optionally at one message)."""
    base_url = config.get("web.base_url")
    if not base_url:
        port = config.get("web.port", 8765)
        base_url = f"http://127.0.0.1:{port}"
    base_url = base_url.rstrip("/")
    if project and msg_id:
        return f"{base_url}/#/p/{project}/m/{msg_id}"
    if project:
        return f"{base_url}/#/p/{project}"
    return base_url


def _fit_for_telegram(text, link, always_link=True):
    """text (+ link) within Telegram's size limit; a cut text ends with an ellipsis and always carries the link."""
    body = f"{text}\n\n{link}" if (link and always_link) else text
    if len(body) <= TG_LIMIT:
        return body
    keep = max(TG_LIMIT - len(link) - 3, 0)
    return f"{text[:keep].rstrip()}…\n\n{link}"


def _telegram_enabled():
    channels = config.get("notify.channels") or []
    if isinstance(channels, str):
        channels = [channels]
    return "telegram" in channels and bool(telegram.get_token() and telegram.get_chat_id())


def _mirror_slot(project):
    """Fixed-window rate limit shared between processes: "send" | "notice" (first message over the limit) | "drop"."""
    verdict = ["send"]

    def apply(state):
        state = state if isinstance(state, dict) else {}
        rec = state.get(project) if isinstance(state.get(project), dict) else {}
        now = _now()
        if not rec or now - rec.get("start", 0) >= MIRROR_WINDOW_S:
            rec = {"start": now, "count": 0, "noticed": False}
        rec["count"] += 1
        if rec["count"] <= MIRROR_MAX_PER_WINDOW:
            verdict[0] = "send"
        elif not rec["noticed"]:
            rec["noticed"] = True
            verdict[0] = "notice"
        else:
            verdict[0] = "drop"
        state[project] = rec
        return state

    store.update_json(os.path.join(paths.home(), "telegram-mirror.json"), apply, default={})
    return verdict[0]


def mirror_enabled(project):
    """True when a chat message of `project` should be mirrored to its Telegram topic at all."""
    try:
        return bool(project) and _telegram_enabled()
    except Exception:
        return False


SEND_TIMEOUT_S = 5  # per Bot API call of a drainer; a row costs at most this for the topic and this for the send


def row_verdict(row):
    """The rate-limit verdict of a mirror row ("send" | "notice" | "drop"), decided once and kept in the row; a
    notification row (question, --notify, system notice) is never rate limited."""
    if row.get("verdict"):
        return row["verdict"]
    if row.get("kind") == "notify":
        return "send"
    return _mirror_slot(row.get("project"))


def _permanent(res):
    """True for a Bot API answer that no retry can change: a 4xx other than 429 (bad request, forbidden, ...)."""
    code = res.get("error_code")
    return isinstance(code, int) and 400 <= code < 500 and code != 429


def send_mirror_row(row):
    """D51/D60: post one outbox row to the project's Telegram topic (created on demand; no project = General).
    Size-limited; a cut text ends with the web chat link. A "mirror" row is rate limited (10 per project per
    minute, then one pointer, then silence; decided once per row and stored in it, so a retry neither consumes
    another slot nor loses the pointer). A "notify" row (a question with its answer buttons, --notify, a system
    notice) is never rate limited, always carries the link, and records the Telegram message id against the chat
    message id, so a button press or a quoted reply finds its question. Never raises.
    -> {"ok": True} | {"ok": False, "drop": reason} (also for a permanent 4xx) |
       {"ok": False, "description": api text, "retry_after": seconds|None}; "verdict" is set whenever decided."""
    project, text, msg_id = row.get("project"), row.get("text") or "", row.get("msg_id")
    notice = row.get("kind") == "notify"
    try:
        if not (_telegram_enabled() if notice else mirror_enabled(project)):
            return {"ok": False, "drop": "telegram is not configured as a notify channel"}
        verdict = row_verdict(row)
        out = {"verdict": verdict}
        if verdict == "drop":
            out.update(ok=False, drop="rate limit: more than %d messages a minute" % MIRROR_MAX_PER_WINDOW)
            return out
        has_topic = bool(project and projects.load_project(project))  # no project record: the General topic
        thread_id = telegram.create_project_topic(project, timeout=SEND_TIMEOUT_S) if has_topic else None
        if has_topic and not thread_id:
            out.update(ok=False, description="could not find or create the project's Telegram topic")
            return out
        buttons = None
        if verdict == "notice":
            body = "… more messages in the web chat: " + web_link(project)
        elif notice:
            body = _fit_for_telegram(text, web_link(project, msg_id))
            buttons = row.get("options") if (row.get("question") and row.get("options")) else None
        else:
            body = _fit_for_telegram(text, web_link(project, msg_id), always_link=False)
        res = telegram.send_message(thread_id, body, buttons=buttons, msg_id=msg_id if notice else None,
                                    timeout=SEND_TIMEOUT_S) or {}
        if res.get("ok"):
            out["ok"] = True
            return out
        code = ("%s: " % res["error_code"]) if res.get("error_code") else ""
        desc = "%s%s" % (code, res.get("description") or "sendMessage failed")
        if _permanent(res):
            out.update(ok=False, drop="refused by Telegram, not retried: " + desc)
            return out
        params = res.get("parameters") if isinstance(res.get("parameters"), dict) else {}
        out.update(ok=False, retry_after=params.get("retry_after"), description=desc)
        return out
    except Exception as exc:
        return {"ok": False, "description": "%s: %s" % (type(exc).__name__, exc)}


def notify(project, text, msg_id=None, question=False, options=None, macos_runner=None):
    """Notify the user through configured channels.

    Args:
        project: Project slug or None (machine-level / General topic).
        text: Notification message.
        msg_id: Optional message ID.
        question: Whether this notification asks a question.
        options: Optional list of options (for question buttons).
        macos_runner: Optional injected command runner for macnotify tests.

    Returns:
        list of dict: Results from each channel.
    """
    channels = config.get("notify.channels") or []
    if isinstance(channels, str):
        channels = [channels]

    link = web_link(project, msg_id)

    results = []

    for ch in channels:
        try:
            if ch == "telegram":
                # D60: queued in the durable outbox (order per project, retries, buttons kept); a drainer sends it
                if not _telegram_enabled():
                    results.append({"channel": "telegram", "ok": False,
                                    "error": "telegram bot token or chat id not configured"})
                    continue
                from foreman import outbox
                row = outbox.enqueue(project, text, msg_id, kind="notify", question=bool(question),
                                     options=list(options or []))
                results.append({"channel": "telegram", "ok": True, "queued": True, "row": row["id"]})

            elif ch == "macos":
                title = f"foreman · {project}" if project else "foreman"
                mac_res = macnotify.notify(title, text, url=link, runner=macos_runner)
                results.append({
                    "channel": "macos",
                    "ok": mac_res.get("ok", False),
                    "backend": mac_res.get("backend"),
                    "result": mac_res,
                })

            elif ch == "file":
                log_path = os.path.join(paths.home(), "notify-log.jsonl")
                entry = {
                    "ts": timeutil.now_iso(),
                    "project": project,
                    "text": text,
                    "msg_id": msg_id,
                    "question": bool(question),
                    "options": list(options or []),
                    "link": link,
                }
                store.append_jsonl(log_path, entry)
                results.append({
                    "channel": "file",
                    "ok": True,
                    "path": log_path,
                })

            else:
                results.append({
                    "channel": ch,
                    "ok": False,
                    "error": f"unknown channel {ch}",
                })

        except Exception as exc:
            results.append({
                "channel": ch,
                "ok": False,
                "error": str(exc),
            })

    return results
