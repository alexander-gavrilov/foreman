"""Fix round 6, bug B (D60): the Telegram mirror goes through a durable outbox."""
import os
import threading
import time
from unittest import mock

from tests.fakes.fake_telegram import FakeTelegramServer
from tests.test_delivery_wake import RuntimeCase
from foreman import messaging, notify, outbox, paths, projects, store, telegram


class OutboxCase(RuntimeCase):
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
        self.clock = [10000.0]
        patcher = mock.patch.object(outbox, "_now", lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)

    def fail_sends(self, n, status=500, description="Internal Server Error: test", retry_after=None):
        real = self.tg.handle_sendMessage
        state = {"left": n}

        def handler(params):
            if state["left"] > 0:
                state["left"] -= 1
                body = {"ok": False, "error_code": status, "description": description}
                if retry_after:
                    body["parameters"] = {"retry_after": retry_after}
                return status, body
            return real(params)
        self.tg.handle_sendMessage = handler

    def sent(self):
        return [m["text"] for m in self.tg.sent_messages]

    def events(self, kind):
        return [e for e in store.read_jsonl(paths.events_file()) if e.get("kind") == kind]


class EnqueueTest(OutboxCase):
    def test_say_only_appends_a_row_and_returns_at_once(self):
        t0 = time.time()
        messaging.say(self.slug, "hello")
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(self.sent(), [])  # nothing is sent by say itself
        rows = outbox.pending()
        self.assertEqual([(r["project"], r["text"], r["attempts"]) for r in rows], [(self.slug, "hello", 0)])
        self.assertTrue(rows[0]["msg_id"] and rows[0]["created_at"])

    def test_an_unreachable_telegram_never_delays_the_reply(self):
        self.tg.stop()
        t0 = time.time()
        messaging.say(self.slug, "hello")
        messaging.user_reply(self.slug, "hi", channel="web")
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(len(outbox.pending()), 2)

    def test_nothing_is_queued_when_telegram_is_not_configured(self):
        self.write_config({"notify": {"channels": ["file"]}})
        messaging.say(self.slug, "quiet")
        self.assertEqual(outbox.pending(), [])
        self.assertFalse(os.path.exists(outbox.path()))

    def test_drain_without_an_outbox_file_touches_nothing(self):
        outbox.drain()
        self.assertEqual(sorted(f for f in os.listdir(paths.home()) if "outbox" in f), [])


class DrainTest(OutboxCase):
    def test_a_row_is_removed_only_after_the_bot_api_said_ok(self):
        self.fail_sends(2)
        messaging.say(self.slug, "hello")
        outbox.drain()
        row = outbox.pending()[0]
        self.assertEqual((self.sent(), row["attempts"]), ([], 1))
        outbox.drain()  # still inside the backoff: no new attempt
        self.assertEqual(outbox.pending()[0]["attempts"], 1)
        self.clock[0] += 120
        outbox.drain()
        self.assertEqual(outbox.pending()[0]["attempts"], 2)
        self.clock[0] += 120
        outbox.drain()
        self.assertEqual((self.sent(), outbox.pending()), (["hello"], []))

    def test_every_failure_is_logged_with_the_api_description_and_never_the_token(self):
        self.fail_sends(1, status=400, description="Bad Request: message thread not found")
        messaging.say(self.slug, "hello")
        outbox.drain()
        rows = self.events("mirror_error")
        self.assertEqual(len(rows), 1)
        self.assertIn("message thread not found", rows[0]["error"])
        self.assertNotIn("TEST_TOKEN", str(store.read_jsonl(paths.events_file())))

    def test_an_api_failure_without_an_exception_is_logged_too(self):
        with mock.patch.object(telegram, "send_message", return_value={"ok": False, "description": "boom"}):
            messaging.say(self.slug, "hello")
            outbox.drain()
        self.assertEqual([e["error"] for e in self.events("mirror_error")][0].count("boom"), 1)

    def test_a_network_error_is_logged_and_retried(self):
        self.tg.stop()
        messaging.say(self.slug, "hello")
        outbox.drain()
        self.assertEqual(len(self.events("mirror_error")), 1)
        self.assertEqual(outbox.pending()[0]["attempts"], 1)

    def test_a_429_honours_retry_after(self):
        self.fail_sends(1, status=429, description="Too Many Requests: retry after 30", retry_after=30)
        messaging.say(self.slug, "hello")
        outbox.drain()
        self.assertGreaterEqual(outbox.pending()[0]["next_at"], self.clock[0] + 30)
        self.clock[0] += 29
        outbox.drain()
        self.assertEqual((self.sent(), outbox.pending()[0]["attempts"]), ([], 1))
        self.clock[0] += 2
        outbox.drain()
        self.assertEqual((self.sent(), outbox.pending()), (["hello"], []))

    def test_order_is_preserved_per_project_while_the_head_is_failing(self):
        self.fail_sends(1)
        for text in ("a", "b", "c"):
            messaging.say(self.slug, text)
        outbox.drain()
        self.assertEqual(self.sent(), [])  # b and c wait behind a
        self.clock[0] += 120
        outbox.drain()
        self.assertEqual(self.sent(), ["a", "b", "c"])

    def test_a_failing_project_does_not_block_another(self):
        other = self.init_project("other")
        proj = projects.load_project(other["slug"])
        proj["telegram_topic_id"] = 66
        projects.save_project(proj)
        real = self.tg.handle_sendMessage

        def handler(params):
            if params.get("message_thread_id") == 55:
                return 500, {"ok": False, "error_code": 500, "description": "down"}
            return real(params)
        self.tg.handle_sendMessage = handler
        messaging.say(self.slug, "a")
        messaging.say(other["slug"], "b")
        outbox.drain()
        self.assertEqual(self.sent(), ["b"])
        self.assertEqual([r["project"] for r in outbox.pending()], [self.slug])

    def test_rows_older_than_an_hour_are_dropped_with_an_event(self):
        self.tg.stop()
        messaging.say(self.slug, "old")
        self.clock[0] += outbox.MAX_AGE_S + 1
        outbox.drain()
        self.assertEqual(outbox.pending(), [])
        drops = self.events("mirror_dropped")
        self.assertEqual(len(drops), 1)
        self.assertIn("1 hour", drops[0]["reason"])

    def test_the_pointer_row_survives_retries_and_is_sent_once(self):
        with mock.patch.object(notify, "MIRROR_MAX_PER_WINDOW", 1):
            messaging.say(self.slug, "m0")
            outbox.drain()
            self.fail_sends(2)
            messaging.say(self.slug, "m1")  # over the limit: the pointer
            messaging.say(self.slug, "m2")  # dropped
            for _ in range(4):
                outbox.drain()
                self.clock[0] += 120
        self.assertEqual(len(self.sent()), 2)
        self.assertIn("web chat", self.sent()[1])
        self.assertEqual(outbox.pending(), [])
        self.assertEqual(len(self.events("mirror_dropped")), 1)  # the rate-limit drop is logged, not silent

    def test_process_exit_before_the_send_leaves_the_row_for_the_daemon(self):
        messaging.say(self.slug, "last words")
        # the CLI died here: nothing drained. A later process (the daemon) sends it.
        self.assertEqual(self.sent(), [])
        self.assertEqual(len(outbox.pending()), 1)
        outbox.drain()
        self.assertEqual(self.sent(), ["last words"])

    def test_two_drainers_never_send_a_row_twice(self):
        real = self.tg.handle_sendMessage

        def slow(params):
            time.sleep(0.15)
            return real(params)
        self.tg.handle_sendMessage = slow
        for text in ("a", "b", "c"):
            messaging.say(self.slug, text)
        threads = [threading.Thread(target=outbox.drain) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        outbox.drain()
        self.assertEqual(self.sent(), ["a", "b", "c"])
        self.assertEqual(outbox.pending(), [])

    def test_a_drainer_that_finds_the_lock_taken_returns_at_once(self):
        messaging.say(self.slug, "a")
        with outbox._drainer_lock() as got:
            self.assertTrue(got)
            self.assertTrue(outbox.drain().get("busy"))
        self.assertEqual(self.sent(), [])

    def test_budget_bounds_the_attempt(self):
        real = self.tg.handle_sendMessage

        def slow(params):
            time.sleep(0.3)
            return real(params)
        self.tg.handle_sendMessage = slow
        for text in "abcdef":
            messaging.say(self.slug, text)
        t0 = time.time()
        outbox.drain(budget_s=0.5)
        self.assertLess(time.time() - t0, 1.5)
        self.assertLess(len(self.sent()), 6)
        self.assertEqual(len(self.sent()) + len(outbox.pending()), 6)


class DaemonTest(OutboxCase):
    def test_the_daemon_drain_thread_sends_rows_and_marks_itself_active(self):
        stop = threading.Event()
        t = outbox.start_drain_thread(stop, interval=0.05)
        self.addCleanup(t.join, 5)
        self.addCleanup(stop.set)
        messaging.say(self.slug, "from the web")
        deadline = time.time() + 10
        while time.time() < deadline and not self.sent():
            time.sleep(0.05)
        self.assertEqual(self.sent(), ["from the web"])
        self.assertTrue(outbox.daemon_active())

    def test_exit_flush_skips_when_a_daemon_is_active_and_sends_otherwise(self):
        messaging.say(self.slug, "a")
        outbox.write_daemon_heartbeat()
        outbox.exit_flush()
        self.assertEqual(self.sent(), [])
        os.unlink(outbox.heartbeat_path())
        outbox.exit_flush()
        self.assertEqual(self.sent(), ["a"])

    def test_a_stale_or_dead_daemon_does_not_count(self):
        outbox.write_daemon_heartbeat()
        self.clock[0] += 600
        self.assertFalse(outbox.daemon_active())
        store.write_json(outbox.heartbeat_path(), {"pid": 2 ** 22 + 12345, "at": self.clock[0]})
        self.assertFalse(outbox.daemon_active())


class ServeDrainsTheOutboxTest(OutboxCase):
    def test_foreman_serve_sends_rows_left_by_a_cli_process(self):
        from foreman.commands import serve
        messaging.say(self.slug, "left by a CLI")  # a CLI process died before sending
        stop = threading.Event()
        t = threading.Thread(target=serve.run_daemon, kwargs={
            "binds": ["127.0.0.1"], "port": 0, "watch_interval": 60, "telegram_timeout": 0.05,
            "stop_event": stop}, daemon=True)
        with mock.patch.object(outbox, "DRAIN_INTERVAL_S", 0.05):
            t.start()
            deadline = time.time() + 10
            while time.time() < deadline and not self.sent():
                time.sleep(0.05)
            stop.set()
            t.join(10)
        self.assertEqual(self.sent(), ["left by a CLI"])


class CliExitTest(OutboxCase):
    def test_a_real_foreman_say_process_sends_its_row_at_exit(self):
        from tests.helpers import run_cli
        code, out, err = run_cli("say", "--project", self.slug, "pong-web")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sent(), ["pong-web"])
        self.assertEqual(outbox.pending(), [])

    def test_a_cli_process_with_an_unreachable_telegram_exits_and_keeps_the_row(self):
        from tests.helpers import run_cli
        self.tg.stop()
        t0 = time.time()
        code, out, err = run_cli("say", "--project", self.slug, "pong-web")
        self.assertEqual(code, 0, err)
        self.assertLess(time.time() - t0, 15)
        self.assertEqual([r["text"] for r in outbox.pending()], ["pong-web"])
        self.assertTrue(self.events("mirror_error"))
