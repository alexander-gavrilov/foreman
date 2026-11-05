"""User notifications dispatcher (owner: WP6, architecture §10).
Dispatches notifications to configured channels (telegram, macos, file).
Failures never raise; they return a list of result dictionaries.
"""
import os

from foreman import config, macnotify, paths, projects, store, telegram, timeutil


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

    # Build web URL link
    base_url = config.get("web.base_url")
    if not base_url:
        port = config.get("web.port", 8765)
        base_url = f"http://127.0.0.1:{port}"
    base_url = base_url.rstrip("/")

    if project and msg_id:
        link = f"{base_url}/#/p/{project}/m/{msg_id}"
    elif project:
        link = f"{base_url}/#/p/{project}"
    else:
        link = base_url

    results = []

    for ch in channels:
        try:
            if ch == "telegram":
                thread_id = None
                if project:
                    proj_rec = projects.load_project(project)
                    thread_id = proj_rec.get("telegram_topic_id") if proj_rec else None

                body = f"{text}\n\n{link}" if link else text
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
