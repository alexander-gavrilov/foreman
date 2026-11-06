"""D51: the project's Telegram topic mirrors the project chat (fake Telegram Bot API)."""
from unittest import mock

from tests.fakes.fake_telegram import FakeTelegramServer
from tests.test_delivery_wake import RuntimeCase
from foreman import messaging, notify, projects, telegram


class ChatMirrorTest(RuntimeCase):
    def setUp(self):
        super().setUp()
        self.tg = FakeTelegramServer(token="TEST_TOKEN", chat_id=-1001234567890)
        self.tg.start()
        self.addCleanup(self.tg.stop)
        self.write_config({
            "notify": {"channels": ["telegram"]},
            "web": {"base_url": "http://ghome.lan:8765"},
            "telegram": {"chat_id": -1001234567890, "api_base": self.tg.base_url, "allowed_user_ids": [12345]},
        })
        telegram.set_token("TEST_TOKEN")
        proj = projects.load_project(self.slug)
        proj["telegram_topic_id"] = 55
        projects.save_project(proj)
        self.orch = self.session(role="orchestrator", title="orch", idle=True, state="idle")

    def texts(self):
        messaging.flush_mirrors(timeout=10)  # the mirror runs in the background, after delivery (N9)
        return [(m["message_thread_id"], m["text"]) for m in self.tg.sent_messages]

    def test_plain_say_is_posted_to_the_topic(self):
        messaging.say(self.slug, "Merged PR #12")
        self.assertEqual(self.texts(), [(55, "Merged PR #12")])
        self.assertIsNone(self.tg.sent_messages[0]["reply_markup"])

    def test_question_is_posted_once_with_buttons_and_link(self):
        msg = messaging.say(self.slug, "Merge?", question=True, options=["Да", "Нет"])
        self.assertEqual(self.tg.sent_messages, [])  # queued (D60), a drainer sends it
        messaging.flush_mirrors(timeout=10)
        self.assertEqual(len(self.tg.sent_messages), 1)
        sent = self.tg.sent_messages[0]
        self.assertIn("/#/p/demo/m/" + msg["id"], sent["text"])
        self.assertEqual(sent["reply_markup"]["inline_keyboard"][0][0]["text"], "Да")

    def test_web_reply_is_posted_prefixed_telegram_reply_is_not_echoed(self):
        messaging.user_reply(self.slug, "go on", channel="web")
        self.assertEqual(self.texts(), [(55, "user (web): go on")])
        messaging.user_reply(self.slug, "from phone", channel="telegram")
        messaging.flush_mirrors(timeout=10)
        self.assertEqual(len(self.tg.sent_messages), 1)

    def test_terminal_gone_notice_reaches_the_topic_for_every_origin(self):
        self.ft.kill(self.orch["transport"]["handle"])
        messaging.user_reply(self.slug, "hello?", channel="telegram")
        self.assertEqual(self.texts(), [(55, messaging.GONE_TEXT + "\n\nhttp://ghome.lan:8765/#/p/demo/m/"
                                         + projects.chat_list(self.slug)[-1]["id"])])
        messaging.user_reply(self.slug, "again", channel="web")
        later = [t for _, t in self.texts()][1:]  # the gone notice is sent at once, the mirror after delivery
        self.assertEqual(sorted(t.startswith(messaging.GONE_TEXT) for t in later), [False, True])
        self.assertIn("user (web): again", later)

    def test_not_mirrored_when_telegram_is_not_a_notify_channel(self):
        self.write_config({"notify": {"channels": ["file"]},
                           "telegram": {"chat_id": -1001234567890, "api_base": self.tg.base_url}})
        messaging.say(self.slug, "quiet")
        messaging.user_reply(self.slug, "hi", channel="web")
        self.assertEqual(self.tg.sent_messages, [])

    def test_long_message_is_cut_and_links_to_the_web_chat(self):
        msg = messaging.say(self.slug, "x" * 9000)
        messaging.flush_mirrors(timeout=10)
        text = self.tg.sent_messages[0]["text"]
        self.assertLessEqual(len(text), 4096)
        self.assertTrue(text.endswith("/#/p/demo/m/" + msg["id"]))
        self.assertIn("…", text)

    def test_rate_limit_suppresses_a_flood_with_one_pointer_to_the_web_chat(self):
        with mock.patch.object(notify, "MIRROR_MAX_PER_WINDOW", 3):
            for i in range(8):
                messaging.say(self.slug, "msg %d" % i)
                messaging.flush_mirrors(timeout=10)  # one at a time: the rate window counts in order
        texts = [t for _, t in self.texts()]
        self.assertEqual(texts[:3], ["msg 0", "msg 1", "msg 2"])
        self.assertEqual(len(texts), 4)
        self.assertIn("web chat", texts[3])
        self.assertIn("/#/p/demo", texts[3])

    def test_rate_limit_window_expires(self):
        clock = [1000.0]
        with mock.patch.object(notify, "MIRROR_MAX_PER_WINDOW", 1), mock.patch.object(notify, "_now", lambda: clock[0]):
            for text in ("a", "b"):
                messaging.say(self.slug, text)
                messaging.flush_mirrors(timeout=10)
            clock[0] += notify.MIRROR_WINDOW_S + 1
            messaging.say(self.slug, "c")
        texts = [t for _, t in self.texts()]
        self.assertEqual(texts[0], "a")
        self.assertIn("web chat", texts[1])
        self.assertEqual(texts[2], "c")

    def test_project_without_topic_gets_one_created(self):
        proj = projects.load_project(self.slug)
        proj.pop("telegram_topic_id")
        projects.save_project(proj)
        messaging.say(self.slug, "hello")
        messaging.flush_mirrors(timeout=10)
        self.assertEqual(len(self.tg.created_topics), 1)
        self.assertEqual(self.tg.sent_messages[0]["message_thread_id"], 100)

    def test_unconfigured_telegram_never_blocks_or_raises(self):
        self.write_config({"notify": {"channels": ["telegram"]}})
        messaging.say(self.slug, "hello")
        messaging.user_reply(self.slug, "hi", channel="web")
        self.assertEqual(self.tg.sent_messages, [])
