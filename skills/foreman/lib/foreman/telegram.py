"""Telegram Bot API channel and poller (owner: WP6, spec §5.3, architecture §10).
Stdlib urllib only, API base from config telegram.api_base, token from secrets.json.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from foreman import config, paths, projects, sessions, store, timeutil


def _state_file():
    return os.path.join(paths.home(), "telegram-state.json")


def _load_state():
    return store.read_json(_state_file(), {"offset": 0, "msg_map": {}})


def set_token(token):
    """Store the bot token in secrets.json with mode 0600."""
    path = paths.secrets_file()

    def apply(sec):
        sec = sec if isinstance(sec, dict) else {}
        sec["telegram_bot_token"] = token
        return sec

    store.update_json(path, apply, default={}, mode=0o600)
    return token


def _last_rejected_user(cid):
    """The user id of the newest update the poller rejected from chat cid, or None."""
    try:
        with open(paths.events_file(), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") == "telegram_unauthorized" and str(event.get("chat_id")) == str(cid):
            if event.get("user_id"):
                return event["user_id"]
    return None


def set_chat(chat_id):
    """Store the supergroup chat_id in config.json and record user from setup message if available."""
    path = paths.config_file()
    try:
        cid = int(chat_id)
    except (ValueError, TypeError):
        cid = chat_id

    # Try to find setup user from recent updates if token is available
    found_users = []
    token = get_token()
    if token:
        try:
            res = _api_call("getUpdates", {"limit": 10}, timeout=5)
            if res and res.get("ok"):
                for u in reversed(res.get("result", [])):
                    msg = u.get("message") or {}
                    if str(msg.get("chat", {}).get("id")) == str(cid):
                        user = msg.get("from", {})
                        if user.get("id"):
                            found_users.append(user["id"])
                            break
        except Exception:
            pass

    # A running daemon consumes updates, so the setup message may only exist as a
    # rejection in events.jsonl. Used only while nobody is allowed yet, so a
    # stranger rejected later can never be added by re-running set-chat.
    if not found_users and not (config.load().get("telegram") or {}).get("allowed_user_ids"):
        rejected = _last_rejected_user(cid)
        if rejected is not None:
            found_users.append(rejected)

    def apply(cfg):
        cfg = cfg if isinstance(cfg, dict) else {}
        tg = cfg.setdefault("telegram", {})
        tg["chat_id"] = cid
        if found_users:
            allowed = tg.setdefault("allowed_user_ids", [])
            for u in found_users:
                if u not in allowed and str(u) not in [str(x) for x in allowed]:
                    allowed.append(u)
        return cfg

    store.update_json(path, apply, default={})
    return cid


def allow_user(user_id):
    """Add a user_id to config telegram.allowed_user_ids."""
    path = paths.config_file()
    try:
        uid = int(user_id)
    except (ValueError, TypeError):
        uid = user_id

    def apply(cfg):
        cfg = cfg if isinstance(cfg, dict) else {}
        tg = cfg.setdefault("telegram", {})
        allowed = tg.setdefault("allowed_user_ids", [])
        if uid not in allowed and str(uid) not in [str(x) for x in allowed]:
            allowed.append(uid)
        return cfg

    store.update_json(path, apply, default={})
    return uid


def get_allowed_user_ids():
    raw = config.get("telegram.allowed_user_ids") or []
    if isinstance(raw, (int, str)):
        raw = [raw]
    return [str(x) for x in raw]


def get_token():
    sec = store.read_json(paths.secrets_file(), {})
    return sec.get("telegram_bot_token") if isinstance(sec, dict) else None


def get_chat_id():
    return config.get("telegram.chat_id")


def get_api_base():
    base = config.get("telegram.api_base") or "https://api.telegram.org"
    return base.rstrip("/")


def get_mapped_msg_id(tg_mid):
    st = _load_state()
    return st.get("msg_map", {}).get(str(tg_mid))


def _api_call(method, payload=None, timeout=15):
    token = get_token()
    if not token:
        return {"ok": False, "description": "telegram_bot_token not configured"}
    base = get_api_base()
    url = f"{base}/bot{token}/{method}"
    headers = {"Content-Type": "application/json"}
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            return json.loads(body)
        except Exception:
            return {"ok": False, "error_code": exc.code, "description": str(exc)}
    except Exception as exc:
        return {"ok": False, "description": str(exc)}


def check():
    """Verify configuration, getMe, getChat, and can_manage_topics permissions."""
    missing = []
    token = get_token()
    if not token:
        missing.append("telegram_bot_token in secrets.json missing")

    chat_id = get_chat_id()
    if not chat_id:
        missing.append("telegram.chat_id in config.json missing")

    if not get_allowed_user_ids():
        missing.append("telegram.allowed_user_ids in config.json is empty: every reply would be dropped "
                       "(send a message in the group, then `foreman telegram set-chat` again, or "
                       "`foreman telegram allow-user <id>`)")

    if missing:
        return {"ok": False, "missing": missing}

    me = _api_call("getMe")
    if not me.get("ok"):
        missing.append(f"getMe failed: {me.get('description', 'unauthorized / network error')}")
        return {"ok": False, "missing": missing}

    bot_info = me.get("result", {})
    bot_id = bot_info.get("id")
    bot_username = bot_info.get("username")

    chat = _api_call("getChat", {"chat_id": chat_id})
    if not chat.get("ok"):
        missing.append(f"getChat failed: {chat.get('description', 'chat not found')}")
        return {"ok": False, "missing": missing}

    chat_info = chat.get("result", {})
    if not chat_info.get("is_forum"):
        missing.append(f"topics (forum) must be enabled for chat {chat_id}")

    member = _api_call("getChatMember", {"chat_id": chat_id, "user_id": bot_id})
    if not member.get("ok"):
        missing.append(f"getChatMember failed: {member.get('description', 'cannot verify permissions')}")
    else:
        mem_info = member.get("result", {})
        status = mem_info.get("status")
        can_topics = mem_info.get("can_manage_topics")
        if status not in ("administrator", "creator") or not can_topics:
            missing.append(f"bot @{bot_username} lacks can_manage_topics permission in chat {chat_id}")

    if missing:
        return {"ok": False, "missing": missing}

    return {
        "ok": True,
        "bot": bot_info,
        "chat": chat_info,
    }


def create_topic(name):
    """Create a forum topic in the machine's group; return its message_thread_id or None if not configured."""
    chat_id = get_chat_id()
    if not chat_id or not get_token():
        return None
    res = _api_call("createForumTopic", {"chat_id": chat_id, "name": name})
    if res and res.get("ok"):
        return res["result"].get("message_thread_id")
    return None


def create_project_topic(project_slug):
    """Ensure a project has a Telegram topic (idempotent). Returns message_thread_id or None."""
    proj = projects.load_project(project_slug)
    if not proj:
        return None
    if proj.get("telegram_topic_id"):
        return proj["telegram_topic_id"]

    name = proj.get("name") or proj["slug"]
    tid = create_topic(name)
    if tid:
        proj["telegram_topic_id"] = tid
        projects.save_project(proj)
    return tid


def send_message(thread_id, text, buttons=None, msg_id=None, timeout=15):
    """Send a message to a topic (None = General topic).

    If buttons are provided, inline_keyboard is attached with callback_data '<msgid>|<option index>'.
    Maps Telegram message_id -> chat msg_id for reply tracking.
    """
    chat_id = get_chat_id()
    if not chat_id:
        return {"ok": False, "description": "telegram.chat_id not configured"}

    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if thread_id is not None:
        payload["message_thread_id"] = int(thread_id)

    if buttons:
        row = []
        for idx, btn in enumerate(buttons):
            if isinstance(btn, dict):
                row.append(btn)
            elif isinstance(btn, (list, tuple)) and len(btn) == 2:
                row.append({"text": str(btn[0]), "callback_data": str(btn[1])})
            elif isinstance(btn, str):
                cb_data = f"{msg_id}|{idx}" if msg_id else f"{idx}"
                row.append({"text": btn, "callback_data": cb_data})
        payload["reply_markup"] = {"inline_keyboard": [row]}

    res = _api_call("sendMessage", payload, timeout=timeout)
    if res and res.get("ok"):
        tg_mid = res.get("result", {}).get("message_id")
        if tg_mid and msg_id:
            def apply(st):
                st = st or {"offset": 0, "msg_map": {}}
                st.setdefault("msg_map", {})[str(tg_mid)] = msg_id
                return st

            store.update_json(_state_file(), apply, default={"offset": 0, "msg_map": {}})
    return res


_CONTENT_TYPES = ("photo", "sticker", "video", "video_note", "voice", "audio", "document", "animation", "location",
                  "venue", "contact", "poll", "dice", "game")
_SERVICE_TYPES = ("forum_topic_created", "forum_topic_edited", "forum_topic_closed", "forum_topic_reopened",
                  "general_forum_topic_hidden", "general_forum_topic_unhidden", "new_chat_members",
                  "left_chat_member", "new_chat_title", "new_chat_photo", "delete_chat_photo", "pinned_message",
                  "message_auto_delete_timer_changed", "write_access_allowed")


def _message_text(msg):
    """The text to route to the orchestrator: text or caption; a marker for media without a caption; None for
    service messages (topic renamed, member joined, pin…), which are not user input."""
    text = msg.get("text") or msg.get("caption") or ""
    if text.strip():
        return text
    for kind in _CONTENT_TYPES:
        if msg.get(kind):
            return "[non-text message: %s]" % kind
    if any(k in msg for k in _SERVICE_TYPES):
        return None
    return "[non-text message: unknown]"


def _callback_option(slug, cb_data):
    """(question msg id, option text) for callback data '<msgid>|<index>', or None if it names no live option."""
    msg_id, sep, idx_str = (cb_data or "").partition("|")
    if not sep:
        return None
    try:
        idx = int(idx_str)
    except ValueError:
        return None
    for row in reversed(projects.chat_list(slug)):
        if row.get("id") == msg_id:
            opts = row.get("options") or []
            if row.get("question") and 0 <= idx < len(opts):
                return msg_id, str(opts[idx])
            return None
    return None


def _find_project_by_topic_id(thread_id):
    if thread_id is None:
        return None
    for p in projects.list_projects():
        top_id = p.get("telegram_topic_id")
        if top_id is not None and str(top_id) == str(thread_id):
            return p
    return None


def _deliver_user_reply(project, text, reply_to=None):
    from foreman import messaging
    return messaging.user_reply(project, text, reply_to=reply_to, channel="telegram")


def _format_machine_status():
    projs = projects.list_projects()
    lines = []
    if projs:
        for p in projs:
            active_sessions = [
                s for s in sessions.list_sessions(p["slug"])
                if sessions.is_live(s)
            ]
            lines.append(f"{p['slug']}: {len(active_sessions)} active session(s)")
    else:
        lines.append("no projects initialised")

    try:
        from foreman import limits
        lines.append("limits: " + limits.summary_line())
    except Exception:
        lines.append("limits: unknown")
    return "\n".join(lines)


def poll_once(timeout=0):
    """One getUpdates long-poll round: route topic messages / button presses to project chats. Returns count."""
    chat_id = get_chat_id()
    token = get_token()
    if not chat_id or not token:
        return 0

    st = _load_state()
    offset = st.get("offset")

    payload = {"timeout": int(timeout)}
    if offset:
        payload["offset"] = int(offset)

    call_timeout = max(int(timeout) + 10, 15)
    res = _api_call("getUpdates", payload, timeout=call_timeout)
    if not res or not res.get("ok"):
        return 0

    updates = res.get("result", [])
    if not updates:
        return 0

    max_update_id = offset or 0
    allowed_users = get_allowed_user_ids()

    for u in updates:
        uid = u.get("update_id")
        if uid is not None and uid >= max_update_id:
            max_update_id = uid + 1

        # 1. Message update
        msg = u.get("message")
        if msg:
            cid = msg.get("chat", {}).get("id")
            from_uid = msg.get("from", {}).get("id")
            if str(cid) != str(chat_id):
                import sys
                sys.stderr.write(f"telegram: update from unknown chat {cid}\n")
                sys.stderr.flush()
                store.append_jsonl(paths.events_file(), {
                    "ts": timeutil.now_iso(),
                    "kind": "telegram_foreign_chat",
                    "chat_id": cid,
                    "user_id": from_uid,
                    "from": msg.get("from"),
                })
                continue

            if str(from_uid) not in allowed_users:
                import sys
                sys.stderr.write(f"telegram: update from unknown user {from_uid} in chat {cid}\n")
                sys.stderr.flush()
                store.append_jsonl(paths.events_file(), {
                    "ts": timeutil.now_iso(),
                    "kind": "telegram_unauthorized",
                    "chat_id": cid,
                    "user_id": from_uid,
                    "from": msg.get("from"),
                })
                continue

            text = _message_text(msg)
            if text is None:
                continue
            thread_id = msg.get("message_thread_id")
            proj = _find_project_by_topic_id(thread_id)

            if proj:
                reply_to = None
                if msg.get("reply_to_message"):
                    tg_replied_id = msg["reply_to_message"].get("message_id")
                    state = _load_state()
                    reply_to = state.get("msg_map", {}).get(str(tg_replied_id))
                _deliver_user_reply(proj["slug"], text, reply_to=reply_to)
            elif thread_id is None:
                # General topic
                cmd = text.strip().split()[0] if text.strip() else ""
                if cmd in ("/status", "/status@foreman_bot") or cmd.startswith("/status"):
                    status_text = _format_machine_status()
                    send_message(None, status_text)

        # 2. Callback query update
        cb = u.get("callback_query")
        if cb:
            cb_id = cb.get("id")
            cb_msg = cb.get("message", {})
            cid = cb_msg.get("chat", {}).get("id")
            from_uid = cb.get("from", {}).get("id")
            if str(cid) != str(chat_id):
                import sys
                sys.stderr.write(f"telegram: callback from unknown chat {cid}\n")
                sys.stderr.flush()
                store.append_jsonl(paths.events_file(), {
                    "ts": timeutil.now_iso(),
                    "kind": "telegram_foreign_chat",
                    "chat_id": cid,
                    "user_id": from_uid,
                    "from": cb.get("from"),
                })
                if cb_id:
                    _api_call("answerCallbackQuery", {"callback_query_id": cb_id})
                continue

            if str(from_uid) not in allowed_users:
                import sys
                sys.stderr.write(f"telegram: callback from unknown user {from_uid} in chat {cid}\n")
                sys.stderr.flush()
                store.append_jsonl(paths.events_file(), {
                    "ts": timeutil.now_iso(),
                    "kind": "telegram_unauthorized",
                    "chat_id": cid,
                    "user_id": from_uid,
                    "from": cb.get("from"),
                })
                if cb_id:
                    _api_call("answerCallbackQuery", {"callback_query_id": cb_id})
                continue

            cb_data = cb.get("data", "")
            thread_id = cb_msg.get("message_thread_id")
            proj = _find_project_by_topic_id(thread_id)

            answer = None
            if proj:
                chosen = _callback_option(proj["slug"], cb_data)
                if chosen is None:
                    answer = "expired"
                elif chosen[1].strip().rstrip(".…").lower() == "ответить":
                    answer = "reply in the topic"  # the "Reply…" button is a prompt, not an answer
                else:
                    _deliver_user_reply(proj["slug"], chosen[1], reply_to=chosen[0])

            if cb_id:
                payload = {"callback_query_id": cb_id}
                if answer:
                    payload["text"] = answer
                _api_call("answerCallbackQuery", payload)

    # Persist updated offset
    def apply_offset(state):
        state = state or {"offset": 0, "msg_map": {}}
        state["offset"] = max_update_id
        return state

    store.update_json(_state_file(), apply_offset, default={"offset": 0, "msg_map": {}})
    return len(updates)
