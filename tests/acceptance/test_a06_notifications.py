"""Spec §8.6 — notifications open the right project's chat; a reply in the project's topic reaches that project's
orchestrator, on Claude Code and on Antigravity alike; on the work Mac a desktop notification opens the same chat
in the local web UI.

Telegram is a local stub HTTP server (never api.telegram.org). Desktop notifications go to command shims on PATH
that only record their argv. Opening the link on a phone over WireGuard is the user-only part (see README)."""
import unittest

from tests.acceptance.common import AcceptanceCase, digests

BASE = "http://ghome.lan:8765"


class TelegramNotificationTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.tg = self.start_telegram()
        self.slug_a, _ = self.make_project("shopping-list")
        self.slug_b, _ = self.make_project("garden-plans")
        self.orch_a = self.start_orchestrator(self.slug_a)                           # Claude Code
        self.orch_b = self.start_orchestrator(self.slug_b, agent="antigravity")      # Antigravity
        from foreman import projects
        self.topic = {s: projects.load_project(s)["telegram_topic_id"] for s in (self.slug_a, self.slug_b)}

    def test_every_project_got_its_own_topic_at_init(self):
        self.assertEqual(sorted(t["name"] for t in self.tg.created_topics), ["garden-plans", "shopping-list"])
        self.assertNotEqual(self.topic[self.slug_a], self.topic[self.slug_b])
        self.assertTrue(all(self.topic.values()))

    def test_a_question_lands_in_the_project_topic_with_a_deep_link_to_its_chat(self):
        msgid = self.fm_ok("say", "--project", self.slug_a, "Мерджить PR #12?", "--question",
                           "--options", "Да|Нет|Ответить…")
        self.assertEqual(self.tg.sent_messages, [])  # D60: queued; the daemon (or the detached drainer) sends it
        self.fm_ok("outbox", "drain", "--budget", "5")
        sent = [m for m in self.tg.sent_messages if m["message_thread_id"] == self.topic[self.slug_a]]
        self.assertEqual(len(sent), 1)
        self.assertIn("Мерджить PR #12?", sent[0]["text"])
        self.assertIn("%s/#/p/%s/m/%s" % (BASE, self.slug_a, msgid), sent[0]["text"])
        self.assertNotIn("/#/p/%s" % self.slug_b, sent[0]["text"])
        self.assertNotIn("token", sent[0]["text"].lower())  # links carry no secret
        buttons = [b["text"] for row in sent[0]["reply_markup"]["inline_keyboard"] for b in row]
        self.assertEqual(buttons, ["Да", "Нет", "Ответить…"])
        # nothing leaked into the other project's topic
        self.assertEqual([m for m in self.tg.sent_messages if m["message_thread_id"] == self.topic[self.slug_b]], [])

    def test_a_topic_reply_reaches_a_claude_orchestrator(self):
        self.tg.enqueue_text_message("да, мерджь и закрывай", thread_id=self.topic[self.slug_a])
        self.assertIn("polled 1", self.fm_ok("telegram", "poll", "--once"))
        got = digests(self.inputs(self.orch_a))
        self.assertEqual(len(got), 1)
        self.assertIn("да, мерджь и закрывай", got[0])
        self.assertIn("USER (telegram)", got[0])
        self.assertEqual(digests(self.inputs(self.orch_b)), [], "the other project's orchestrator must not hear it")

    def test_a_topic_reply_reaches_an_antigravity_orchestrator(self):
        self.tg.enqueue_text_message("сначала проверь тесты", thread_id=self.topic[self.slug_b])
        self.fm_ok("telegram", "poll", "--once")
        got = digests(self.inputs(self.orch_b))
        self.assertEqual(len(got), 1)
        self.assertIn("сначала проверь тесты", got[0])
        self.assertEqual(digests(self.inputs(self.orch_a)), [])
        self.assert_no_model_started()

    def test_a_button_press_is_a_reply_with_that_option(self):
        msgid = self.fm_ok("say", "--project", self.slug_a, "Деплоить?", "--question", "--options", "Да|Нет")
        self.tg.enqueue_callback_query("%s|1" % msgid, thread_id=self.topic[self.slug_a])
        self.fm_ok("telegram", "poll", "--once")
        got = digests(self.inputs(self.orch_a))
        self.assertEqual(len(got), 1)
        self.assertIn("Нет", got[0])
        self.assertEqual(len(self.tg.answered_callbacks), 1)

    def test_strangers_and_foreign_chats_are_ignored(self):
        self.tg.enqueue_text_message("rm -rf", thread_id=self.topic[self.slug_a], user_id=999)
        self.tg.enqueue_text_message("hi", chat_id=-100777, thread_id=self.topic[self.slug_a])
        self.fm_ok("telegram", "poll", "--once")
        self.assertEqual(digests(self.inputs(self.orch_a)), [])

    def test_a_reply_with_the_orchestrator_terminal_gone_is_reported_not_lost(self):
        self.terminal().kill(self.orch_a["transport"]["handle"])
        self.tg.enqueue_text_message("ты тут?", thread_id=self.topic[self.slug_a])
        self.fm_ok("telegram", "poll", "--once")
        from foreman import projects
        chat = projects.chat_list(self.slug_a)
        self.assertEqual([r["from"] for r in chat], ["user", "foreman"])
        self.assertIn("закрыт", chat[-1]["text"])

    def test_a_codex_orchestrator_is_refused_with_the_documented_reason(self):
        slug, _ = self.make_project("third")
        code, out, err = self.fm("orchestrator", "start", "--project", slug, "--agent", "codex", "--transport", "fake")
        self.assertEqual(code, 2)
        self.assertIn("worker-only in v1", err)


class DesktopNotificationTest(AcceptanceCase):
    """The work Mac: no Telegram, `terminal-notifier -open <url>` into the local web UI."""

    def setUp(self):
        super().setUp()
        self.write_config({"upstream": {"mode": "draft"},
                           "machine": {"name": "work-mac", "kind": "work"},
                           "web": {"base_url": "http://localhost:8765", "port": 8765, "bind": ["127.0.0.1"]}})
        self.slug, _ = self.make_project("billing")
        self.orch = self.start_orchestrator(self.slug)

    def test_work_machine_defaults_to_macos_notifications_without_telegram(self):
        from foreman import config
        self.assertEqual(config.get("notify.channels"), ["macos"])
        self.assertIsNone(config.get("telegram.chat_id"))

    def test_the_notification_opens_the_right_chat_in_the_local_web_ui(self):
        msgid = self.fm_ok("say", "--project", self.slug, "Нужен выбор схемы", "--question", "--options", "A|B")
        calls = self.shim_calls("terminal-notifier")
        self.assertEqual(len(calls), 1)
        argv = calls[0]["argv"]
        self.assertEqual(argv[argv.index("-open") + 1], "http://localhost:8765/#/p/%s/m/%s" % (self.slug, msgid))
        self.assertIn("Нужен выбор схемы", argv[argv.index("-message") + 1])
        self.assertIn(self.slug, argv[argv.index("-title") + 1])

    def test_without_terminal_notifier_the_osascript_text_carries_the_url(self):
        import os
        os.unlink(os.path.join(self.shim_dir, "terminal-notifier"))
        msgid = self.fm_ok("say", "--project", self.slug, "Вопрос", "--question", "--options", "A|B")
        calls = self.shim_calls("osascript")
        self.assertEqual(len(calls), 1)
        self.assertIn("http://localhost:8765/#/p/%s/m/%s" % (self.slug, msgid), calls[0]["argv"][1])


if __name__ == "__main__":
    unittest.main()
