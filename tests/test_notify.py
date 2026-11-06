"""Unit tests for user notifications dispatcher (owner: WP6, spec §5.3, architecture §10)."""
import os
import unittest

from tests.helpers import ForemanTestCase
from tests.fakes.fake_telegram import FakeTelegramServer
from foreman import config, notify, outbox, paths, projects, store, telegram


class NotifyTestCase(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.server = FakeTelegramServer(token="TEST_TOKEN", chat_id=-1001234567890)
        self.server.start()
        self.addCleanup(self.server.stop)

        self.write_config({
            "notify": {"channels": ["telegram"]},
            "web": {"base_url": "http://ghome.lan:8765"},
            "telegram": {
                "chat_id": -1001234567890,
                "api_base": self.server.base_url,
            },
        })
        telegram.set_token("TEST_TOKEN")

    def test_notify_telegram_project_topic(self):
        proj = self.init_project("demo")
        proj["telegram_topic_id"] = 77
        projects.save_project(proj)

        res = notify.notify("demo", "Worker finished", msg_id="m-finish1")
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["ok"])
        self.assertTrue(res[0]["queued"])  # D60: queued in the outbox, sent by a drainer
        self.assertEqual(self.server.sent_messages, [])
        outbox.drain()

        self.assertEqual(len(self.server.sent_messages), 1)
        sent = self.server.sent_messages[0]
        self.assertEqual(sent["message_thread_id"], 77)
        self.assertIn("Worker finished", sent["text"])
        self.assertIn("http://ghome.lan:8765/#/p/demo/m/m-finish1", sent["text"])
        self.assertIsNone(sent["reply_markup"])

    def test_notify_telegram_question_with_options(self):
        proj = self.init_project("demo")
        proj["telegram_topic_id"] = 88
        projects.save_project(proj)

        res = notify.notify(
            "demo",
            "Merge PR?",
            msg_id="m-q42",
            question=True,
            options=["Да", "Нет"],
        )
        self.assertTrue(res[0]["ok"])
        outbox.drain()

        self.assertEqual(len(self.server.sent_messages), 1)
        sent = self.server.sent_messages[0]
        markup = sent["reply_markup"]
        self.assertIsNotNone(markup)
        row = markup["inline_keyboard"][0]
        self.assertEqual(row[0]["text"], "Да")
        self.assertEqual(row[0]["callback_data"], "m-q42|0")
        self.assertEqual(row[1]["text"], "Нет")
        self.assertEqual(row[1]["callback_data"], "m-q42|1")

    def test_notify_telegram_machine_level_general_topic(self):
        res = notify.notify(None, "Limit warning: weekly 72%", msg_id="m-lim1")
        self.assertTrue(res[0]["ok"])
        outbox.drain()

        self.assertEqual(len(self.server.sent_messages), 1)
        sent = self.server.sent_messages[0]
        self.assertIsNone(sent["message_thread_id"])
        self.assertIn("Limit warning: weekly 72%", sent["text"])
        self.assertNotIn("token", sent["text"].lower())

    def test_notify_macos_channel(self):
        self.write_config({
            "notify": {"channels": ["macos"]},
            "web": {"base_url": "http://127.0.0.1:8765"},
        })
        recorded = []

        def runner(cmd):
            recorded.append(cmd)

        res = notify.notify("demo", "Session alert", msg_id="m-mac1", macos_runner=runner)
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["ok"])
        self.assertEqual(len(recorded), 1)
        cmd_str = " ".join(recorded[0])
        self.assertIn("Session alert", cmd_str)

    def test_notify_file_channel(self):
        self.write_config({"notify": {"channels": ["file"]}})
        res = notify.notify("demo", "File log", msg_id="m-file1", question=True, options=["OK"])
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["ok"])

        log_path = os.path.join(paths.home(), "notify-log.jsonl")
        self.assertTrue(os.path.exists(log_path))
        rows = store.read_jsonl(log_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project"], "demo")
        self.assertEqual(rows[0]["text"], "File log")
        self.assertEqual(rows[0]["msg_id"], "m-file1")
        self.assertTrue(rows[0]["question"])
        self.assertEqual(rows[0]["options"], ["OK"])

    def test_notify_failures_never_raise(self):
        # an unreachable Telegram: the call returns at once with the row queued, the drainer logs the failure
        self.write_config({
            "notify": {"channels": ["telegram"]},
            "telegram": {
                "chat_id": -1001234567890,
                "api_base": "http://127.0.0.1:1",  # connection refused
            },
        })
        res = notify.notify("demo", "Will fail silently", msg_id="m-fail")
        self.assertEqual(len(res), 1)
        self.assertTrue(res[0]["ok"])
        self.assertIn("channel", res[0])
        outbox.drain()
        self.assertEqual(len(outbox.pending()), 1)  # kept for the next attempt
        events = store.read_jsonl(paths.events_file())
        self.assertTrue(any(e.get("kind") == "mirror_error" for e in events))

    def test_notify_without_a_bot_token_reports_the_failure(self):
        os.unlink(paths.secrets_file())
        res = notify.notify("demo", "No token", msg_id="m-x")
        self.assertFalse(res[0]["ok"])
        self.assertIn("error", res[0])
        self.assertEqual(outbox.pending(), [])


if __name__ == "__main__":
    unittest.main()
