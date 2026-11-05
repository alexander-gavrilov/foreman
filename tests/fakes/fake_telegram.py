"""Fake Telegram Bot API server for testing (owner: WP6).
Binds to 127.0.0.1 on an OS-assigned port (0), implements getMe, getChat,
getChatMember, getUpdates with offset semantics, sendMessage recording,
createForumTopic, and answerCallbackQuery.
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse


class FakeTelegramHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Silence standard HTTP access logging in tests
        pass

    def _send_json(self, status, data):
        blob = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _parse_request(self):
        pr = urlparse(self.path)
        m = re.match(r"^/bot([^/]+)/(\w+)$", pr.path)
        if not m:
            return None, None, {}
        token, method = m.group(1), m.group(2)
        params = {}
        if pr.query:
            qs = parse_qs(pr.query)
            params = {k: v[0] if len(v) == 1 else v for k, v in qs.items()}

        content_length = int(self.headers.get("Content-Length") or 0)
        if content_length > 0:
            body = self.rfile.read(content_length)
            content_type = self.headers.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    params.update(json.loads(body.decode("utf-8")))
                except ValueError:
                    pass
            elif "application/x-www-form-urlencoded" in content_type:
                qs = parse_qs(body.decode("utf-8"))
                params.update({k: v[0] if len(v) == 1 else v for k, v in qs.items()})

        return token, method, params

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def _dispatch(self):
        server = self.server.fake_server
        token, method, params = self._parse_request()
        if not method:
            self._send_json(404, {"ok": False, "error_code": 404, "description": "Not Found"})
            return

        if server.expected_token is not None and token != server.expected_token:
            self._send_json(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
            return

        handler_name = f"handle_{method}"
        handler = getattr(server, handler_name, None)
        if not handler:
            self._send_json(404, {"ok": False, "error_code": 404, "description": f"Unknown method {method}"})
            return

        status, resp = handler(params)
        self._send_json(status, resp)


class FakeTelegramServer:
    def __init__(self, token="TEST_BOT_TOKEN", chat_id=-1001234567890, chat_title="foreman · test"):
        self.expected_token = token
        self.chat_id = chat_id
        self.chat_title = chat_title
        self.bot_id = 987654321
        self.bot_name = "Foreman Bot"
        self.bot_username = "foreman_bot"
        self.is_forum = True
        self.can_manage_topics = True

        self._next_thread_id = 100
        self._next_msg_id = 1
        self._next_update_id = 1

        self.updates = []
        self.sent_messages = []
        self.created_topics = []
        self.answered_callbacks = []
        self.get_updates_calls = []

        self._httpd = None
        self._thread = None
        self.port = None

    def start(self):
        self._httpd = HTTPServer(("127.0.0.1", 0), FakeTelegramHandler)
        self._httpd.fake_server = self
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"

    # Handlers
    def handle_getMe(self, params):
        return 200, {
            "ok": True,
            "result": {
                "id": self.bot_id,
                "is_bot": True,
                "first_name": self.bot_name,
                "username": self.bot_username,
            },
        }

    def handle_getChat(self, params):
        cid = params.get("chat_id")
        if str(cid) != str(self.chat_id):
            return 400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        return 200, {
            "ok": True,
            "result": {
                "id": self.chat_id,
                "title": self.chat_title,
                "type": "supergroup",
                "is_forum": self.is_forum,
            },
        }

    def handle_getChatMember(self, params):
        cid = params.get("chat_id")
        uid = params.get("user_id")
        if str(cid) != str(self.chat_id):
            return 400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        return 200, {
            "ok": True,
            "result": {
                "status": "administrator" if self.can_manage_topics else "member",
                "can_manage_topics": self.can_manage_topics,
                "user": {
                    "id": self.bot_id,
                    "is_bot": True,
                    "first_name": self.bot_name,
                    "username": self.bot_username,
                },
            },
        }

    def handle_createForumTopic(self, params):
        cid = params.get("chat_id")
        name = params.get("name")
        if str(cid) != str(self.chat_id):
            return 400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
        if not self.can_manage_topics:
            return 403, {"ok": False, "error_code": 403, "description": "Forbidden: not enough rights to manage topics"}
        tid = self._next_thread_id
        self._next_thread_id += 1
        rec = {"chat_id": cid, "name": name, "message_thread_id": tid}
        self.created_topics.append(rec)
        return 200, {
            "ok": True,
            "result": {
                "message_thread_id": tid,
                "name": name,
                "icon_color": 7322096,
            },
        }

    def handle_sendMessage(self, params):
        cid = params.get("chat_id")
        text = params.get("text", "")
        thread_id = params.get("message_thread_id")
        reply_markup = params.get("reply_markup")
        if isinstance(reply_markup, str):
            try:
                reply_markup = json.loads(reply_markup)
            except ValueError:
                pass

        mid = self._next_msg_id
        self._next_msg_id += 1
        rec = {
            "message_id": mid,
            "chat_id": cid,
            "text": text,
            "message_thread_id": thread_id,
            "reply_markup": reply_markup,
        }
        self.sent_messages.append(rec)
        return 200, {
            "ok": True,
            "result": {
                "message_id": mid,
                "chat": {"id": cid},
                "message_thread_id": thread_id,
                "text": text,
                "date": 1700000000,
            },
        }

    def handle_answerCallbackQuery(self, params):
        cb_id = params.get("callback_query_id")
        text = params.get("text")
        self.answered_callbacks.append({"callback_query_id": cb_id, "text": text})
        return 200, {"ok": True, "result": True}

    def handle_getUpdates(self, params):
        offset = params.get("offset")
        timeout = params.get("timeout", 0)
        offset_val = int(offset) if offset is not None else None
        self.get_updates_calls.append({"offset": offset_val, "timeout": int(timeout)})

        matching = []
        for u in self.updates:
            if offset_val is None or u["update_id"] >= offset_val:
                matching.append(u)
        return 200, {"ok": True, "result": matching}

    # Test helpers to queue events
    def enqueue_update(self, update_dict):
        if "update_id" not in update_dict:
            update_dict["update_id"] = self._next_update_id
            self._next_update_id += 1
        self.updates.append(update_dict)
        return update_dict

    def enqueue_text_message(
        self,
        text,
        chat_id=None,
        thread_id=None,
        message_id=None,
        reply_to_message_id=None,
        user_id=12345,
    ):
        if chat_id is None:
            chat_id = self.chat_id
        mid = message_id or self._next_msg_id
        if mid >= self._next_msg_id:
            self._next_msg_id = mid + 1

        msg = {
            "message_id": mid,
            "from": {"id": user_id, "first_name": "User"},
            "chat": {"id": chat_id, "type": "supergroup"},
            "date": 1700000000,
            "text": text,
        }
        if thread_id is not None:
            msg["message_thread_id"] = thread_id
            msg["is_topic_message"] = True
        if reply_to_message_id is not None:
            msg["reply_to_message"] = {
                "message_id": reply_to_message_id,
                "date": 1699999990,
                "text": "original bot message",
            }

        return self.enqueue_update({"message": msg})

    def enqueue_callback_query(
        self,
        callback_data,
        callback_id=None,
        chat_id=None,
        thread_id=None,
        message_id=None,
        user_id=12345,
    ):
        if chat_id is None:
            chat_id = self.chat_id
        cid = callback_id or f"cb_{self._next_msg_id}"
        mid = message_id or self._next_msg_id
        if mid >= self._next_msg_id:
            self._next_msg_id = mid + 1

        msg = {
            "message_id": mid,
            "from": {"id": self.bot_id, "is_bot": True},
            "chat": {"id": chat_id, "type": "supergroup"},
            "date": 1700000000,
            "text": "Question text",
        }
        if thread_id is not None:
            msg["message_thread_id"] = thread_id

        cb = {
            "id": cid,
            "from": {"id": user_id, "first_name": "User"},
            "message": msg,
            "data": callback_data,
        }
        return self.enqueue_update({"callback_query": cb})
