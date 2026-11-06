"""Fix round 7 (review 6 F1, F2, F9): no flush from hooks, a detached bounded drainer, questions and --notify in
the outbox, blank text and permanent 4xx."""
import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from unittest import mock

from tests.helpers import BIN, run_cli
from tests.test_fix6_outbox import OutboxCase
from foreman import hooks, messaging, notify, outbox, paths, projects, sessions, store, telegram


@contextlib.contextmanager
def blackhole():
    """A TCP port that accepts connections (the kernel backlog) and never answers: a blackholed Telegram."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(16)
    try:
        yield "http://127.0.0.1:%d" % srv.getsockname()[1]
    finally:
        srv.close()


def drainer_env(case, **extra):
    env = {"HOME": case.home, "FOREMAN_HOME": case.foreman_home, "FOREMAN_FAKE_TRANSPORT": case.fake_transport}
    env.update(extra)
    return env


class NoFlushTest(OutboxCase):
    def test_nothing_flushes_at_exit_any_more(self):
        self.assertFalse(hasattr(outbox, "exit_flush"))
        import atexit  # noqa: F401
        messaging.say(self.slug, "queued")
        with mock.patch.object(outbox, "drain", side_effect=AssertionError("drained")):
            hooks.run_hook("claude", "Stop", json.dumps({"session_id": "x", "cwd": "/"}), env={})

    def test_a_hook_process_stays_fast_with_a_blackholed_telegram_and_a_pending_row(self):
        with blackhole() as base:
            self.write_config({"notify": {"channels": ["telegram"]}, "telegram": {
                "chat_id": -1001234567890, "api_base": base, "allowed_user_ids": [1]}})
            messaging.say(self.slug, "queued")
            self.assertEqual(len(outbox.pending()), 1)
            env = dict(os.environ, **drainer_env(self), FOREMAN_OUTBOX_SPAWN="1")
            t0 = time.time()
            proc = subprocess.run([sys.executable, BIN, "hook", "--agent", "claude", "--event", "PreToolUse"],
                                  input=json.dumps({"session_id": "unknown", "cwd": "/tmp", "tool_name": "Read"}),
                                  capture_output=True, text=True, env=env, timeout=30)
            took = time.time() - t0
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertLess(took, 1.5, "a hook gained latency: %.2f s" % took)
        self.assertEqual(outbox.pending()[0]["attempts"], 0)  # the hook did not touch the row


class KickTest(OutboxCase):
    def say_cli(self, *args):
        from tests.test_fix7_adopt import run_main
        return run_main("say", "--project", self.slug, *args)

    def test_a_cli_that_queued_a_row_starts_one_detached_drainer(self):
        outbox._queued_here = False
        code, out, err = self.say_cli("hello")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.spawned.call_count, 1)
        self.assertEqual(self.sent(), [])  # the CLI itself sent nothing

    def test_no_drainer_when_a_fresh_daemon_is_on_it(self):
        outbox._queued_here = False
        outbox.write_daemon_heartbeat()
        self.say_cli("hello")
        self.assertEqual(self.spawned.call_count, 0)

    def test_no_second_drainer_while_one_holds_the_lock(self):
        outbox._queued_here = False
        with outbox._drainer_lock() as got:
            self.assertTrue(got)
            self.say_cli("hello")
        self.assertEqual(self.spawned.call_count, 0)

    def test_nothing_queued_nothing_started(self):
        outbox._queued_here = False
        self.write_config({"notify": {"channels": ["file"]}})
        self.say_cli("hello")
        self.assertEqual(self.spawned.call_count, 0)

    def test_a_hook_never_kicks(self):
        outbox._queued_here = True  # even if something in the hook process had queued a row
        outbox.enqueue(self.slug, "x", "m1")
        from tests.test_fix7_adopt import run_main
        run_main("hook", "--agent", "claude", "--event", "Stop")
        self.assertEqual(self.spawned.call_count, 0)

    def test_the_off_switch(self):
        outbox._queued_here = False
        with mock.patch.dict(os.environ, {"FOREMAN_OUTBOX_SPAWN": "0"}):
            self.say_cli("hello")
        self.assertEqual(self.spawned.call_count, 0)


class DetachedDrainerTest(OutboxCase):
    def test_drain_detached_sleeps_through_a_backoff_within_its_budget_and_sends(self):
        self.fail_sends(1)
        with mock.patch.object(outbox, "BACKOFF_S", (0.2,)), mock.patch.object(outbox, "_now", time.time):
            messaging.say(self.slug, "later")
            outbox.drain_detached(budget_s=10)
        self.assertEqual(self.sent(), ["later"])
        self.assertEqual(outbox.pending(), [])

    def test_drain_detached_stops_at_its_budget_and_leaves_the_rest(self):
        with blackhole() as base:
            self.write_config({"notify": {"channels": ["telegram"]}, "telegram": {
                "chat_id": -1001234567890, "api_base": base, "allowed_user_ids": [1]}})
            with mock.patch.object(notify, "SEND_TIMEOUT_S", 0.2), mock.patch.object(outbox, "_now", time.time):
                messaging.say(self.slug, "x")
                t0 = time.time()
                outbox.drain_detached(budget_s=1.0)
                took = time.time() - t0
        self.assertLess(took, 5)
        self.assertEqual(len(outbox.pending()), 1)

    def test_a_row_queued_while_the_drainer_finishes_is_not_stranded(self):
        """The drainer looks at the file again after it let go of the lock."""
        messaging.say(self.slug, "a")
        real = outbox.drain
        calls = []

        def drain(*a, **kw):
            res = real(*a, **kw)
            if not calls:
                calls.append(1)
                outbox.enqueue(self.slug, "b", "m-b")  # queued by another CLI right after the lock was released
            return res
        with mock.patch.object(outbox, "drain", drain):
            outbox.drain_detached(budget_s=10)
        self.assertEqual(self.sent(), ["a", "b"])

    def test_a_drainer_that_finds_the_lock_taken_leaves_at_once(self):
        messaging.say(self.slug, "a")
        with outbox._drainer_lock():
            t0 = time.time()
            outbox.drain_detached(budget_s=30)
            self.assertLess(time.time() - t0, 2)
        self.assertEqual(self.sent(), [])

    def test_the_hidden_subcommand_runs_a_real_detached_process_end_to_end(self):
        env = drainer_env(self, FOREMAN_OUTBOX_SPAWN="1", FOREMAN_OUTBOX_BUDGET_S="20")
        t0 = time.time()
        code, out, err = run_cli("say", "--project", self.slug, "pong-web", env=env)
        took = time.time() - t0
        self.assertEqual(code, 0, err)
        self.assertLess(took, 5)
        deadline = time.time() + 20
        while time.time() < deadline and (self.sent() != ["pong-web"] or outbox.pending()):
            time.sleep(0.1)
        self.assertEqual(self.sent(), ["pong-web"])
        self.assertEqual(outbox.pending(), [])

    def test_say_with_a_blackholed_telegram_returns_at_once_and_keeps_the_row(self):
        with blackhole() as base:
            self.write_config({"notify": {"channels": ["telegram"]}, "telegram": {
                "chat_id": -1001234567890, "api_base": base, "allowed_user_ids": [1]}})
            env = drainer_env(self, FOREMAN_OUTBOX_SPAWN="1", FOREMAN_OUTBOX_BUDGET_S="1")
            t0 = time.time()
            code, out, err = run_cli("say", "--project", self.slug, "hello", env=env)
            took = time.time() - t0
        self.assertEqual(code, 0, err)
        self.assertLess(took, 2.0, "say blocked: %.2f s" % took)
        self.assertEqual([r["text"] for r in outbox.pending()], ["hello"])


class WriteBeforeSendTest(OutboxCase):
    def test_a_killed_drainer_neither_resends_at_once_nor_hides_the_failure(self):
        messaging.say(self.slug, "x")
        with mock.patch.object(notify, "send_mirror_row", side_effect=KeyboardInterrupt):  # killed mid-send
            with self.assertRaises(KeyboardInterrupt):
                outbox.drain()
        row = outbox.pending()[0]
        self.assertEqual(row["attempts"], 1)
        self.assertTrue(row["inflight"])
        self.assertGreater(row["next_at"], self.clock[0])
        outbox.drain()  # the next drainer: still inside the retry time, nothing is sent
        self.assertEqual(self.sent(), [])
        self.clock[0] += 120
        outbox.drain()
        self.assertEqual(self.sent(), ["x"])
        errors = [e["error"] for e in self.events("mirror_error")]
        self.assertTrue(any("did not finish" in e for e in errors), errors)

    def test_the_retry_time_covers_the_longest_send(self):
        messaging.say(self.slug, "x")
        seen = {}

        def look(row):
            seen.update(outbox.pending()[0])
            return {"ok": True}
        with mock.patch.object(notify, "send_mirror_row", side_effect=look):
            outbox.drain()
        self.assertGreaterEqual(seen["next_at"] - self.clock[0], outbox.SEND_WORST_S)
        self.assertEqual(seen["attempts"], 1)


class QuestionsTest(OutboxCase):
    def ask(self, text="Deploy?", options=("Да", "Нет")):
        return messaging.say(self.slug, text, question=True, options=list(options))

    def test_a_question_is_queued_with_its_buttons_and_sent_by_the_drainer(self):
        msg = self.ask()
        self.assertEqual(self.sent(), [])
        row = outbox.pending()[0]
        self.assertEqual((row["kind"], row["question"], row["options"], row["msg_id"]),
                         ("notify", True, ["Да", "Нет"], msg["id"]))
        outbox.drain()
        sent = self.tg.sent_messages[0]
        self.assertIn("Deploy?", sent["text"])
        self.assertIn("http://ghome.lan:8765/#/p/%s/m/%s" % (self.slug, msg["id"]), sent["text"])
        keyboard = sent["reply_markup"]["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in keyboard], ["Да", "Нет"])
        self.assertEqual(keyboard[0]["callback_data"], "%s|0" % msg["id"])
        self.assertEqual(sent["message_thread_id"], 55)

    def test_a_button_press_and_a_quoted_reply_still_find_the_question(self):
        msg = self.ask()
        outbox.drain()
        tg_mid = self.tg.sent_messages[0]["message_id"]
        self.assertEqual(telegram.get_mapped_msg_id(tg_mid), msg["id"])
        self.tg.enqueue_callback_query("%s|1" % msg["id"], thread_id=55, user_id=12345)
        telegram.poll_once()
        chat = projects.chat_list(self.slug)
        self.assertEqual([(r["text"], r["reply_to"]) for r in chat if r.get("from") == "user"],
                         [("Нет", msg["id"])])
        self.assertEqual(projects.chat_list(self.slug)[0]["answered_by"], chat[-1]["id"])
        self.tg.enqueue_text_message("because", thread_id=55, reply_to_message_id=tg_mid, user_id=12345)
        telegram.poll_once()
        last = projects.chat_list(self.slug)[-1]
        self.assertEqual((last["text"], last.get("reply_to")), ("because", msg["id"]))

    def test_notify_flag_goes_through_the_outbox_too(self):
        messaging.say(self.slug, "heads up", notify_user=True)
        self.assertEqual(self.sent(), [])
        self.assertEqual([r["kind"] for r in outbox.pending()], ["notify"])
        outbox.drain()
        self.assertEqual(len(self.tg.sent_messages), 1)
        self.assertIsNone(self.tg.sent_messages[0]["reply_markup"])

    def test_order_per_project_between_mirror_rows_and_a_question(self):
        messaging.say(self.slug, "a")
        self.ask("q?")
        messaging.say(self.slug, "b")
        outbox.drain()
        self.assertEqual([t.split("\n")[0] for t in self.sent()], ["a", "q?", "b"])

    def test_a_failing_question_blocks_its_project_in_order_and_is_retried(self):
        self.fail_sends(1)
        self.ask("q?")
        messaging.say(self.slug, "after")
        outbox.drain()
        self.assertEqual(self.sent(), [])
        self.clock[0] += 120
        outbox.drain()
        self.assertEqual([t.split("\n")[0] for t in self.sent()], ["q?", "after"])

    def test_questions_are_never_rate_limited(self):
        with mock.patch.object(notify, "MIRROR_MAX_PER_WINDOW", 2):
            for n in range(5):
                self.ask("q%d?" % n)
            outbox.drain()
        self.assertEqual(len(self.sent()), 5)

    def test_a_blackholed_telegram_never_blocks_a_question_and_it_arrives_after_recovery(self):
        real_base = self.tg.base_url
        with blackhole() as base:
            self.write_config({"notify": {"channels": ["telegram"]}, "web": {"base_url": "http://ghome.lan:8765"},
                               "telegram": {"chat_id": -1001234567890, "api_base": base, "allowed_user_ids": [1]}})
            t0 = time.time()
            self.ask()
            self.assertLess(time.time() - t0, 1.0)
            with mock.patch.object(notify, "SEND_TIMEOUT_S", 0.2):
                outbox.drain()
            self.assertEqual(len(outbox.pending()), 1)
            self.assertTrue(self.events("mirror_error"))
        self.write_config({"notify": {"channels": ["telegram"]}, "web": {"base_url": "http://ghome.lan:8765"},
                           "telegram": {"chat_id": -1001234567890, "api_base": real_base, "allowed_user_ids": [1]}})
        self.clock[0] += 120
        outbox.drain()
        self.assertEqual(len(self.tg.sent_messages), 1)
        self.assertEqual(outbox.pending(), [])

    def test_the_cli_question_does_not_block_with_a_dead_telegram(self):
        with blackhole() as base:
            self.write_config({"notify": {"channels": ["telegram"]}, "telegram": {
                "chat_id": -1001234567890, "api_base": base, "allowed_user_ids": [1]}})
            t0 = time.time()
            code, out, err = run_cli("say", "--project", self.slug, "--question", "--options", "Да|Нет", "ok?",
                                     env=drainer_env(self))
            took = time.time() - t0
        self.assertEqual(code, 0, err)
        self.assertLess(took, 2.0, "say --question blocked: %.2f s" % took)
        self.assertEqual([(r["kind"], r["options"]) for r in outbox.pending()], [("notify", ["Да", "Нет"])])

    def test_notify_for_machine_level_notices_uses_the_general_topic(self):
        notify.notify(None, "limit reached")
        outbox.drain()
        self.assertIsNone(self.tg.sent_messages[0]["message_thread_id"])


class PoisonRowTest(OutboxCase):
    def test_blank_text_is_refused(self):
        from foreman.cliutil import CliError
        for blank in ("", " ", "\n\t"):
            with self.assertRaises(CliError):
                messaging.say(self.slug, blank)
        self.assertEqual(outbox.pending(), [])
        self.assertEqual(projects.chat_list(self.slug), [])
        code, out, err = run_cli("say", "--project", self.slug, " ", env=drainer_env(self))
        self.assertNotEqual(code, 0)

    def test_a_permanent_400_drops_the_head_row_at_once_and_the_next_rows_go_out(self):
        self.fail_sends(1, status=400, description="Bad Request: text must be non-empty")
        for text in ("poison", "good1", "good2"):
            messaging.say(self.slug, text)
        outbox.drain()
        self.assertEqual(self.sent(), ["good1", "good2"])
        self.assertEqual(outbox.pending(), [])
        drops = self.events("mirror_dropped")
        self.assertEqual(len(drops), 1)
        self.assertIn("400", drops[0]["reason"])
        self.assertIn("non-empty", drops[0]["reason"])

    def test_403_is_retried_not_dropped(self):  # review 7 N1: a fixable cause must not wipe the outbox
        self.fail_sends(1, status=403, description="Forbidden: bot was kicked")
        messaging.say(self.slug, "x")
        outbox.drain()
        self.assertEqual(len(outbox.pending()), 1)
        self.assertEqual(self.events("mirror_dropped"), [])

    def test_429_and_5xx_are_still_retried(self):
        for status in (429, 500, 502):
            self.fail_sends(1, status=status, description="try later", retry_after=5 if status == 429 else None)
            messaging.say(self.slug, "x%d" % status)
            outbox.drain()
            self.assertEqual(len(outbox.pending()), 1, status)
            self.clock[0] += 200
            outbox.drain()
            self.assertEqual(outbox.pending(), [], status)
        self.assertEqual(self.events("mirror_dropped"), [])
