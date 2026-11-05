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


def mirror_to_topic(project, text, msg_id=None):
    """D51: post one project-chat message to the project's Telegram topic (created on demand). Only when telegram
    is a notify channel and configured. Rate- and size-limited; the web chat link marks what was cut. Failures never
    raise. -> {"ok": bool, ...}"""
    try:
        if not project or not _telegram_enabled():
            return {"ok": False, "skipped": True}
        thread_id = telegram.create_project_topic(project)
        if not thread_id:
            return {"ok": False, "error": "no topic"}
        verdict = _mirror_slot(project)
        if verdict == "drop":
            return {"ok": False, "dropped": True}
        if verdict == "notice":
            body = "… more messages in the web chat: " + web_link(project)
        else:
            body = _fit_for_telegram(text, web_link(project, msg_id), always_link=False)
        res = telegram.send_message(thread_id, body, timeout=5)
        return {"ok": bool(res and res.get("ok")), "result": res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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
