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


def send_mirror_row(row):
    """D51/D60: post one outbox row to the project's Telegram topic (created on demand). Size-limited; the web chat
    link marks what was cut. The rate limit (10 per project per minute, then one pointer, then silence) is decided
    once per row and returned as `verdict`, so a retry neither consumes another slot nor loses the pointer.
    Never raises. -> {"ok": True} | {"ok": False, "drop": reason} | {"ok": False, "description": api text,
    "retry_after": seconds|None}; "verdict" is set whenever it was decided."""
    project, text, msg_id = row.get("project"), row.get("text") or "", row.get("msg_id")
    try:
        if not mirror_enabled(project):
            return {"ok": False, "drop": "telegram is not configured as a notify channel"}
        verdict = row.get("verdict")
        if not verdict:
            verdict = _mirror_slot(project)
        out = {"verdict": verdict}
        if verdict == "drop":
            out.update(ok=False, drop="rate limit: more than %d messages a minute" % MIRROR_MAX_PER_WINDOW)
            return out
        thread_id = telegram.create_project_topic(project)
        if not thread_id:
            out.update(ok=False, description="could not find or create the project's Telegram topic")
            return out
        if verdict == "notice":
            body = "… more messages in the web chat: " + web_link(project)
        else:
            body = _fit_for_telegram(text, web_link(project, msg_id), always_link=False)
        res = telegram.send_message(thread_id, body, timeout=5) or {}
        if res.get("ok"):
            out["ok"] = True
            return out
        params = res.get("parameters") if isinstance(res.get("parameters"), dict) else {}
        out.update(ok=False, retry_after=params.get("retry_after"),
                   description="%s%s" % (("%s: " % res["error_code"]) if res.get("error_code") else "",
                                         res.get("description") or "sendMessage failed"))
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
                thread_id = None
                if project:
                    proj_rec = projects.load_project(project)
                    thread_id = proj_rec.get("telegram_topic_id") if proj_rec else None

                body = _fit_for_telegram(text, link)
                buttons = options if (question and options) else None
                tg_res = telegram.send_message(thread_id, body, buttons=buttons, msg_id=msg_id)
                ok = bool(tg_res and tg_res.get("ok"))
                results.append({
                    "channel": "telegram",
                    "ok": ok,
                    "result": tg_res,
                })

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
