"""Fix round 8 (review 7): N1 retryable 4xx keep their rows, N2 a detached drainer never strands rows, N3 adopt and
the drainer's environment."""
import os
import time
from unittest import mock

from tests.test_fix6_child import start
from tests.test_fix6_outbox import OutboxCase
from tests.test_fix7_adopt import run_main
from tests.test_fix7_outbox import blackhole
from tests.test_hooks import HookCase, claude
from foreman import messaging, notify, outbox, paths, projects, telegram

REAL_SPAWN = outbox.spawn_drainer  # test setUp replaces outbox.spawn_drainer by a mock; this is the real one


class RetryableFourXxTest(OutboxCase):
    def check_kept_then_delivered(self, status, description):
        self.fail_sends(1, status=status, description=description)
        messaging.say(self.slug, "m1")
        messaging.say(self.slug, "m2")
        notify.notify(self.slug, "need an answer", question=True, options=["Да", "Нет"])
        outbox.drain()
        self.assertEqual(len(outbox.pending()), 3, (status, description))
        self.assertEqual(self.events("mirror_dropped"), [])
        errors = [e["error"] for e in self.events("mirror_error")]
        self.assertTrue(any(str(status) in e for e in errors), errors)  # logged, with the API's own words
        self.clock[0] += 200  # the bad token was fixed / the permission granted
        outbox.drain()
        self.assertEqual(outbox.pending(), [])
        self.assertEqual(self.sent()[:2], ["m1", "m2"])
        self.assertEqual(len(self.sent()), 3)
        self.assertEqual(self.tg.sent_messages[2].get("reply_markup", {}).get("inline_keyboard", [[]])[0][0]["text"],
                         "Да")

    def test_401_keeps_every_row_and_retries(self):
        self.check_kept_then_delivered(401, "Unauthorized")

    def test_403_keeps_every_row_and_retries(self):
        self.check_kept_then_delivered(403, "Forbidden: bot is not a member of the supergroup chat")

    def test_404_keeps_every_row_and_retries(self):
        self.check_kept_then_delivered(404, "Not Found")

    def test_chat_not_found_is_retried(self):
        self.check_kept_then_delivered(400, "Bad Request: chat not found")

    def test_thread_not_found_is_retried(self):
        self.check_kept_then_delivered(400, "Bad Request: message thread not found")

    def test_one_drain_pass_does_not_wipe_the_outbox_on_401(self):
        self.fail_sends(1000, status=401, description="Unauthorized")
        for text in ("a", "b", "c"):
            messaging.say(self.slug, text)
        for _ in range(3):
            outbox.drain()
            self.clock[0] += 70
        self.assertEqual(len(outbox.pending()), 3)
        self.assertGreaterEqual(len(self.events("mirror_error")), 3)

    def test_a_row_that_keeps_failing_with_401_still_expires_after_an_hour(self):
        self.fail_sends(10000, status=401, description="Unauthorized")
        messaging.say(self.slug, "x")
        outbox.drain()
        self.clock[0] += outbox.MAX_AGE_S + 1
        outbox.drain()
        self.assertEqual(outbox.pending(), [])
        drops = self.events("mirror_dropped")
        self.assertEqual(len(drops), 1)
        self.assertIn("1 hour", drops[0]["reason"])

    def test_payload_level_400_is_still_dropped_at_once(self):
        for description in ("Bad Request: text must be non-empty", "Bad Request: message is too long",
                            "Bad Request: can't parse entities"):
            self.fail_sends(1, status=400, description=description)
            messaging.say(self.slug, "poison")
            messaging.say(self.slug, "good")
            outbox.drain()
            self.assertEqual(outbox.pending(), [], description)
            self.assertEqual(self.sent()[-1:], ["good"])
        self.assertEqual(len(self.events("mirror_dropped")), 3)

    def test_a_deleted_topic_is_forgotten_and_created_again(self):
        self.fail_sends(1, status=400, description="Bad Request: message thread not found")
        messaging.say(self.slug, "into the void")
        outbox.drain()
        self.assertIsNone(projects.load_project(self.slug).get("telegram_topic_id"))
        self.assertEqual(len(outbox.pending()), 1)
        self.clock[0] += 200
        outbox.drain()
        self.assertEqual(outbox.pending(), [])
        self.assertEqual(len(self.tg.created_topics), 1)
        new_id = projects.load_project(self.slug)["telegram_topic_id"]
        self.assertTrue(new_id and new_id != 55)
        self.assertEqual(self.tg.sent_messages[-1]["message_thread_id"], new_id)

    def test_a_topic_id_that_was_replaced_meanwhile_is_not_cleared(self):
        self.fail_sends(1, status=400, description="Bad Request: message thread not found")
        messaging.say(self.slug, "x")
        real = telegram.send_message

        def send(thread_id, *a, **kw):
            proj = projects.load_project(self.slug)
            proj["telegram_topic_id"] = 777  # somebody re-created the topic while this send was in flight
            projects.save_project(proj)
            return real(thread_id, *a, **kw)
        with mock.patch.object(telegram, "send_message", send):
            outbox.drain()
        self.assertEqual(projects.load_project(self.slug)["telegram_topic_id"], 777)


class DetachedSuccessorTest(OutboxCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(outbox, "_now", time.time)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_last_pass_before_exit_sends_what_is_due(self):
        messaging.say(self.slug, "a")
        outbox.drain_detached(budget_s=0)
        self.assertEqual(self.sent(), ["a"])

    def test_a_row_queued_while_the_last_send_finishes_the_budget_is_not_stranded(self):
        messaging.say(self.slug, "m1")
        real = outbox.drain
        calls = []

        def drain(*a, **kw):
            res = real(*a, **kw)
            if not calls:
                calls.append(1)
                outbox.enqueue(self.slug, "m2", "m-2")  # a second CLI sees the drainer alive and starts nothing
                time.sleep(0.5)  # ... and the budget runs out before the drainer comes back
            return res
        with mock.patch.object(outbox, "drain", drain):
            outbox.drain_detached(budget_s=0.3)
        self.assertEqual(self.sent(), ["m1", "m2"])
        self.assertEqual(self.spawned.call_count, 0)

    def test_rows_that_cannot_go_out_yet_get_one_successor(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        t0 = time.time()
        outbox.drain_detached(budget_s=0.5)
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(len(outbox.pending()), 1)
        self.assertEqual(self.spawned.call_count, 1)

    def test_a_far_next_at_neither_spins_nor_gets_forgotten(self):
        self.fail_sends(1, status=429, description="Too Many Requests: retry after 3000", retry_after=3000)
        messaging.say(self.slug, "x")
        real = outbox.drain
        with mock.patch.object(outbox, "drain", wraps=real) as spy:
            t0 = time.time()
            outbox.drain_detached(budget_s=0.5)
            took = time.time() - t0
        self.assertLess(took, 5)
        self.assertLessEqual(spy.call_count, 4)  # it does not call drain in a tight loop
        self.assertEqual(self.spawned.call_count, 1)
        self.assertEqual(len(outbox.pending()), 1)

    def test_no_successor_when_nothing_is_left(self):
        messaging.say(self.slug, "x")
        outbox.drain_detached(budget_s=5)
        self.assertEqual(outbox.pending(), [])
        self.assertEqual(self.spawned.call_count, 0)

    def test_no_successor_when_a_daemon_is_on_it(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        outbox.write_daemon_heartbeat()
        outbox.drain_detached(budget_s=0.3)
        self.assertEqual(self.spawned.call_count, 0)

    def test_no_successor_when_another_drainer_holds_the_lock(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        real = outbox.drain

        def drain(*a, **kw):
            res = real(*a, **kw)
            self.holder = outbox._drainer_lock()
            self.holder.__enter__()  # another process takes the drain lock right after this one let go
            return res
        self.holder = None
        try:
            with mock.patch.object(outbox, "drain", drain):
                outbox.drain_detached(budget_s=0.3)
        finally:
            if self.holder:
                self.holder.__exit__(None, None, None)
        self.assertEqual(self.spawned.call_count, 0)

    def test_the_off_switch_stops_successors_too(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        with mock.patch.dict(os.environ, {"FOREMAN_OUTBOX_SPAWN": "0"}):
            outbox.drain_detached(budget_s=0.3)
        self.assertEqual(self.spawned.call_count, 0)

    def test_the_chain_of_successors_is_bounded(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        with mock.patch.dict(os.environ, {"FOREMAN_OUTBOX_GEN": str(outbox.MAX_SUCCESSORS)}):
            outbox.drain_detached(budget_s=0.3)
        self.assertEqual(self.spawned.call_count, 0)

    def test_a_successor_knows_its_generation(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        with mock.patch.dict(os.environ, {"FOREMAN_OUTBOX_GEN": "3"}):
            outbox.drain_detached(budget_s=0.3)
        self.spawned.assert_called_once_with(generation=4)

    def test_only_one_detached_drainer_lives_at_a_time(self):
        messaging.say(self.slug, "a")
        with outbox._alive_lock() as alive:
            self.assertTrue(alive)
            self.assertTrue(outbox.drainer_running())  # a CLI that queues a row starts nothing
            t0 = time.time()
            outbox.drain_detached(budget_s=30)  # a second one leaves at once and leaves no successor behind
            self.assertLess(time.time() - t0, 2)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.spawned.call_count, 0)
        self.assertFalse(outbox.drainer_running())

    def test_a_drainer_naps_without_holding_the_drain_lock_but_stays_the_only_one(self):
        self.fail_sends(1000, status=500)
        messaging.say(self.slug, "x")
        seen = []
        real_sleep = time.sleep

        def sleep(s):
            seen.append(outbox.drainer_running())
            real_sleep(min(s, 0.05))
        with mock.patch.object(outbox.time, "sleep", sleep):
            outbox.drain_detached(budget_s=0.3)
        self.assertTrue(seen and all(seen), seen)


class SpawnEnvTest(OutboxCase):
    def test_spawn_drainer_passes_foreman_home_explicitly(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FOREMAN_HOME", None)
            with paths.pinned_home(self.foreman_home), mock.patch.object(outbox.subprocess, "Popen") as popen:
                self.assertTrue(REAL_SPAWN())
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["FOREMAN_HOME"], self.foreman_home)
        self.assertEqual(env["FOREMAN_OUTBOX_GEN"], "0")

    def test_the_generation_is_passed_on(self):
        with mock.patch.object(outbox.subprocess, "Popen") as popen:
            REAL_SPAWN(generation=7)
        self.assertEqual(popen.call_args.kwargs["env"]["FOREMAN_OUTBOX_GEN"], "7")


class AdoptResumedPastSessionTest(HookCase):
    def test_adopt_then_resume_of_a_past_session_rebinds_and_a_later_child_stays_a_child(self):
        orch, env = self.pending_launch(role="orchestrator", title="orch", state="busy",
                                        agent_session_id="orch-1", transcript_path="/tmp/orch-1.jsonl")
        cwd, sid = self.proj["path"], orch["sid"]
        self.hook("claude", "SessionStart", start("orch-2", cwd, source="clear"), env)  # /clear: orch-1 -> orch-2
        self.assertEqual(self.rec(sid)["agent_session_id"], "orch-2")
        self.assertIn("orch-1", self.rec(sid)["past_agent_session_ids"])
        # orch-2 crashed; the user ran `claude --resume orch-1` in the same terminal, then `orchestrator adopt`
        code, out, err = run_main("orchestrator", "adopt", sid, "--project", self.slug)
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.rec(sid).get("agent_session_id"))
        self.assertEqual(self.rec(sid).get("past_agent_session_ids") or [], [])
        self.hook("claude", "SessionStart", start("orch-1", cwd, source="resume"), env)
        self.assertEqual(self.rec(sid)["agent_session_id"], "orch-1")
        self.assertNotIn("orch-1", self.rec(sid).get("past_agent_session_ids") or [])
        # a plain `claude -p` child of that terminal can no longer claim the record
        self.hook("claude", "SessionStart", start("kid-1", cwd, source="startup"), env)
        self.assertEqual(self.rec(sid)["agent_session_id"], "orch-1")
        self.assertEqual(self.rec(sid)["role"], "orchestrator")

    def test_adopt_of_a_record_without_a_session_id_still_clears_the_past_ids(self):
        orch, env = self.pending_launch(role="orchestrator", title="orch", state="busy")
        self.sessions.update_session(orch["sid"], past_agent_session_ids=["old-1"])
        code, out, err = run_main("orchestrator", "adopt", orch["sid"], "--project", self.slug)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.rec(orch["sid"]).get("past_agent_session_ids") or [], [])


class RejectedHumansForeignChatTest(OutboxCase):
    """Reviewer's claim: `_rejected_humans` should consider telegram_foreign_chat rows only if of this chat. It reads
    only telegram_unauthorized events and filters them by chat; foreign-chat events are by definition of ANOTHER
    chat and are never read, so a stranger writing in another group is not offered by set-chat."""

    def test_a_foreign_chat_event_is_never_a_candidate(self):
        from foreman import store, timeutil
        store.append_jsonl(paths.events_file(), {
            "ts": timeutil.now_iso(), "kind": "telegram_foreign_chat", "chat_id": -999,
            "user_id": 666, "from": {"id": 666, "first_name": "Stranger"}})
        self.assertEqual(telegram._rejected_humans(-1001234567890), [])
        self.assertEqual(telegram._rejected_humans(-999), [])
