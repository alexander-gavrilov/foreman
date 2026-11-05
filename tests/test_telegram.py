"""Unit and integration tests for Telegram bot operations (owner: WP6)."""
import os
import stat
import unittest

from tests.helpers import ForemanTestCase
from tests.fakes.fake_telegram import FakeTelegramServer
from foreman import config, paths, projects, sessions, store, telegram


class TelegramTestCase(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.server = FakeTelegramServer(token="TEST_TOKEN", chat_id=-1001234567890)
        self.server.start()
        self.addCleanup(self.server.stop)

        # Configure telegram in config and secrets
        self.write_config({
            "telegram": {
                "chat_id": -1001234567890,
                "api_base": self.server.base_url,
                "allowed_user_ids": [12345],
            }
        })
        telegram.set_token("TEST_TOKEN")

    def test_set_token_permissions_and_secrets(self):
        telegram.set_token("MY_SECRET_TOKEN")
        sec_file = paths.secrets_file()
        self.assertTrue(os.path.exists(sec_file))

        # Check file mode 0600
        mode = stat.S_IMODE(os.stat(sec_file).st_mode)
        self.assertEqual(mode, 0o600)

        data = store.read_json(sec_file)
        self.assertEqual(data.get("telegram_bot_token"), "MY_SECRET_TOKEN")

    def test_set_chat(self):
        telegram.set_chat("-100999888777")
        cfg = config.load()
        self.assertEqual(cfg["telegram"]["chat_id"], -100999888777)

    def test_check_missing_token(self):
        # Remove secrets.json
        if os.path.exists(paths.secrets_file()):
            os.remove(paths.secrets_file())
        res = telegram.check()
        self.assertFalse(res["ok"])
        self.assertTrue(any("token" in m for m in res["missing"]))

    def test_check_missing_chat(self):
        self.write_config({"telegram": {"chat_id": None, "api_base": self.server.base_url}})
        res = telegram.check()
        self.assertFalse(res["ok"])
        self.assertTrue(any("chat_id" in m for m in res["missing"]))

    def test_check_success(self):
        res = telegram.check()
        self.assertTrue(res["ok"], f"Expected ok, got: {res}")
        self.assertEqual(res["bot"]["username"], "foreman_bot")

    def test_check_fails_if_not_forum(self):
        self.server.is_forum = False
        res = telegram.check()
        self.assertFalse(res["ok"])
        self.assertTrue(any("forum" in m or "topics" in m for m in res["missing"]))

    def test_check_fails_if_no_can_manage_topics(self):
        self.server.can_manage_topics = False
        res = telegram.check()
        self.assertFalse(res["ok"])
        self.assertTrue(any("can_manage_topics" in m for m in res["missing"]))

    def test_create_topic(self):
        thread_id = telegram.create_topic("demo-project")
        self.assertIsNotNone(thread_id)
        self.assertEqual(len(self.server.created_topics), 1)
        self.assertEqual(self.server.created_topics[0]["name"], "demo-project")

    def test_create_project_topic_idempotent(self):
        proj = self.init_project("demo")
        self.assertIsNone(proj.get("telegram_topic_id"))

        tid1 = telegram.create_project_topic("demo")
        self.assertIsNotNone(tid1)

        proj_updated = projects.load_project("demo")
        self.assertEqual(proj_updated.get("telegram_topic_id"), tid1)

        # Second call should be idempotent and not call create_topic again
        tid2 = telegram.create_project_topic("demo")
        self.assertEqual(tid1, tid2)
        self.assertEqual(len(self.server.created_topics), 1)

    def test_send_message_with_buttons_and_mapping(self):
        res = telegram.send_message(
            thread_id=123,
            text="Please confirm action",
            buttons=["Да", "Нет"],
            msg_id="m-abc12345",
        )
        self.assertTrue(res.get("ok"))
        self.assertEqual(len(self.server.sent_messages), 1)
        sent = self.server.sent_messages[0]
        self.assertEqual(sent["message_thread_id"], 123)
        self.assertEqual(sent["text"], "Please confirm action")

        # Check inline keyboard format
        markup = sent["reply_markup"]
        self.assertIn("inline_keyboard", markup)
        rows = markup["inline_keyboard"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0]["text"], "Да")
        self.assertEqual(rows[0][0]["callback_data"], "m-abc12345|0")
        self.assertEqual(rows[0][1]["text"], "Нет")
        self.assertEqual(rows[0][1]["callback_data"], "m-abc12345|1")

        # Verify mapping telegram msg_id -> chat msg_id is stored
        tg_mid = sent["message_id"]
        mapped = telegram.get_mapped_msg_id(tg_mid)
        self.assertEqual(mapped, "m-abc12345")

    def test_poll_topic_message_routing(self):
        self.init_project("demo")
        thread_id = telegram.create_project_topic("demo")
        proj = projects.load_project("demo")

        # Create active orchestrator session
        orch = sessions.create_session("demo", "claude", role="orchestrator", state="idle")
        proj["orchestrator"] = orch["sid"]
        projects.save_project(proj)

        # Enqueue text message from user in this topic
        self.server.enqueue_text_message("Here is my input", thread_id=thread_id)

        count = telegram.poll_once(timeout=0)
        self.assertEqual(count, 1)

        # Check project chat.jsonl
        chat = projects.chat_list("demo")
        self.assertEqual(len(chat), 1)
        self.assertEqual(chat[0]["text"], "Here is my input")
        self.assertEqual(chat[0]["channel"], "telegram")
        self.assertEqual(chat[0]["from"], "user")

        # Check orchestrator inbox
        inbox = sessions.inbox_all(orch["sid"])
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["text"], "Here is my input")

    def _topic_project(self):
        self.init_project("demo")
        thread_id = telegram.create_project_topic("demo")
        orch = sessions.create_session("demo", "claude", role="orchestrator", state="idle")
        proj = projects.load_project("demo")
        proj["orchestrator"] = orch["sid"]
        projects.save_project(proj)
        return thread_id, orch

    def _raw_topic_message(self, thread_id, **fields):
        msg = {"message_id": 900, "from": {"id": 12345}, "chat": {"id": self.server.chat_id, "type": "supergroup"},
               "date": 1700000000, "message_thread_id": thread_id, "is_topic_message": True}
        msg.update(fields)
        self.server.enqueue_update({"message": msg})

    def test_non_text_topic_message_is_delivered_as_a_marker(self):
        thread_id, orch = self._topic_project()
        self._raw_topic_message(thread_id, sticker={"file_id": "x"})
        self._raw_topic_message(thread_id, photo=[{"file_id": "y"}])                     # no caption
        self._raw_topic_message(thread_id, photo=[{"file_id": "y"}], caption="look")      # caption is text
        self._raw_topic_message(thread_id, voice={"file_id": "v"})
        telegram.poll_once(timeout=0)
        texts = [m["text"] for m in projects.chat_list("demo") if m["from"] == "user"]
        self.assertEqual(texts, ["[non-text message: sticker]", "[non-text message: photo]", "look",
                                 "[non-text message: voice]"])
        self.assertNotIn("", [m["text"] for m in sessions.inbox_all(orch["sid"])])

    def test_service_messages_are_not_delivered(self):
        thread_id, orch = self._topic_project()
        self._raw_topic_message(thread_id, forum_topic_edited={"name": "renamed"})
        self._raw_topic_message(thread_id, pinned_message={"message_id": 1})
        telegram.poll_once(timeout=0)
        self.assertEqual(projects.chat_list("demo"), [])

    def test_poll_reply_to_bot_message_maps_reply_to(self):
        self.init_project("demo")
        thread_id = telegram.create_project_topic("demo")
        proj = projects.load_project("demo")
        orch = sessions.create_session("demo", "claude", role="orchestrator", state="idle")
        proj["orchestrator"] = orch["sid"]
        projects.save_project(proj)

        # Bot sends question message
        res = telegram.send_message(thread_id, "Question?", msg_id="m-q1")
        bot_tg_mid = res["result"]["message_id"]

        # User replies in telegram to that bot message
        self.server.enqueue_text_message(
            "My reply",
            thread_id=thread_id,
            reply_to_message_id=bot_tg_mid,
        )

        telegram.poll_once(timeout=0)

        chat = projects.chat_list("demo")
        self.assertEqual(len(chat), 1)
        self.assertEqual(chat[0]["text"], "My reply")
        self.assertEqual(chat[0]["reply_to"], "m-q1")

    def test_poll_callback_query(self):
        self.init_project("demo")
        thread_id = telegram.create_project_topic("demo")
        proj = projects.load_project("demo")
        orch = sessions.create_session("demo", "claude", role="orchestrator", state="idle")
        proj["orchestrator"] = orch["sid"]
        projects.save_project(proj)

        # Append original question to chat
        q_msg = projects.chat_append(
            "demo", "orchestrator", "Do you accept?", question=True, options=["Да", "Нет"]
        )

        # Enqueue callback query for option 0 ("Да")
        self.server.enqueue_callback_query(
            callback_data=f"{q_msg['id']}|0",
            thread_id=thread_id,
        )

        count = telegram.poll_once(timeout=0)
        self.assertEqual(count, 1)

        # Callback query answered
        self.assertEqual(len(self.server.answered_callbacks), 1)

        # Chat has reply with chosen option text "Да" and reply_to = q_msg['id']
        chat = projects.chat_list("demo")
        self.assertEqual(len(chat), 2)
        reply = chat[-1]
        self.assertEqual(reply["text"], "Да")
        self.assertEqual(reply["reply_to"], q_msg["id"])

    def test_poll_offset_persistence(self):
        self.server.enqueue_text_message("msg 1", thread_id=999)
        self.server.enqueue_text_message("msg 2", thread_id=999)

        telegram.poll_once(timeout=0)

        calls = self.server.get_updates_calls
        self.assertEqual(len(calls), 1)
        self.assertIsNone(calls[0]["offset"])

        # Second poll should use offset = last_id + 1
        telegram.poll_once(timeout=0)
        self.assertEqual(len(self.server.get_updates_calls), 2)
        self.assertEqual(self.server.get_updates_calls[1]["offset"], 3)

    def test_foreign_chat_ignored_and_logged(self):
        # Enqueue message from another chat
        self.server.enqueue_text_message("foreign chat msg", chat_id=-999999999)

        count = telegram.poll_once(timeout=0)
        self.assertEqual(count, 1)

        # No chat messages created
        self.assertEqual(len(projects.list_projects()), 0)

        # Check events.jsonl has log
        events = store.read_jsonl(paths.events_file())
        self.assertTrue(any(e.get("kind") == "telegram_foreign_chat" for e in events))

    def test_general_topic_status(self):
        self.init_project("alpha")
        # Enqueue /status in General topic (no thread_id)
        self.server.enqueue_text_message("/status", thread_id=None)

        count = telegram.poll_once(timeout=0)
        self.assertEqual(count, 1)

        # Bot should have answered with status
        self.assertEqual(len(self.server.sent_messages), 1)
        sent = self.server.sent_messages[0]
        self.assertIsNone(sent["message_thread_id"])
        self.assertIn("alpha", sent["text"])
        self.assertIn("limits:", sent["text"])

    def test_general_topic_non_status_ignored(self):
        # Non-status in General topic is ignored
        self.server.enqueue_text_message("random hello", thread_id=None)

        telegram.poll_once(timeout=0)
        self.assertEqual(len(self.server.sent_messages), 0)

    def test_non_allowed_user_in_right_chat_is_ignored(self):
        self.init_project("demo")
        thread_id = telegram.create_project_topic("demo")
        proj = projects.load_project("demo")
        orch = sessions.create_session("demo", "claude", role="orchestrator", state="idle")
        proj["orchestrator"] = orch["sid"]
        projects.save_project(proj)

        # Enqueue text message from non-allowed user 99999 in the right chat
        self.server.enqueue_text_message("Malicious or unknown input", thread_id=thread_id, user_id=99999)

        count = telegram.poll_once(timeout=0)
        self.assertEqual(count, 1)

        # Project chat and orchestrator inbox must be empty
        chat = projects.chat_list("demo")
        self.assertEqual(len(chat), 0)
        inbox = sessions.inbox_all(orch["sid"])
        self.assertEqual(len(inbox), 0)

        # Event must be logged
        events = store.read_jsonl(paths.events_file())
        self.assertTrue(any(e.get("kind") in ("telegram_unauthorized", "telegram_foreign_user") and e.get("user_id") == 99999 for e in events))

    def test_allow_user_command_and_api(self):
        from tests.helpers import run_cli
        # Call allow-user CLI
        code, out, err = run_cli("telegram", "allow-user", "444555")
        self.assertEqual(code, 0)
        self.assertIn("444555", out)

        cfg = config.load()
        self.assertIn(444555, [int(u) for u in cfg["telegram"]["allowed_user_ids"]])

    def test_set_chat_records_allowed_user_from_setup_message(self):
        # User 888999 sends setup message in the chat
        self.server.enqueue_text_message("setup", chat_id=-100888999000, user_id=888999)
        telegram.set_chat("-100888999000")

        cfg = config.load()
        self.assertEqual(cfg["telegram"]["chat_id"], -100888999000)
        self.assertIn(888999, [int(u) for u in cfg["telegram"].get("allowed_user_ids", [])])



    def test_cli_telegram_commands(self):
        from tests.helpers import run_cli

        # 1. set-token via stdin
        code, out, err = run_cli("telegram", "set-token", stdin="CLI_BOT_TOKEN\n")
        self.assertEqual(code, 0)
        self.assertIn("telegram token saved", out)
        self.assertEqual(telegram.get_token(), "CLI_BOT_TOKEN")

        # 2. set-chat
        code, out, err = run_cli("telegram", "set-chat", str(self.server.chat_id))
        self.assertEqual(code, 0)
        self.assertIn(f"telegram chat set to {self.server.chat_id}", out)

        # Update server expected token to match CLI_BOT_TOKEN
        self.server.expected_token = "CLI_BOT_TOKEN"

        # 3. check
        code, out, err = run_cli("telegram", "check")
        self.assertEqual(code, 0)
        self.assertIn("ok: bot @foreman_bot", out)

        # 4. topic
        self.init_project("cli-proj")
        code, out, err = run_cli("telegram", "topic", "cli-proj")
        self.assertEqual(code, 0)
        self.assertIn("topic:", out)

        # 5. poll --once
        code, out, err = run_cli("telegram", "poll", "--once")
        self.assertEqual(code, 0)
        self.assertIn("polled 0 update(s)", out)


if __name__ == "__main__":
    unittest.main()
