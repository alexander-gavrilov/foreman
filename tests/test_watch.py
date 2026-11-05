import json
import os
import unittest
from unittest.mock import patch, MagicMock

from tests.helpers import ForemanTestCase, run_cli


class WatchTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"  # fixtures' limit resets_at must stay in the future
        self.init_project("demo")

    def test_quiet_state_zero_wakes_and_zero_transport_sends(self):
        from foreman import sessions, watch, wake, transports

        fake_t = transports.get_transport("fake")
        handle = fake_t.launch("claude", "haiku", None, self.work, "hi", "t1")

        sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                state="idle", title="s1")

        with patch("foreman.wake.wake") as mock_wake, \
             patch("foreman.notify.notify") as mock_notify, \
             patch.object(fake_t, "send") as mock_send:
            summary = watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)
            self.assertEqual(mock_notify.call_count, 0)
            self.assertEqual(mock_send.call_count, 0)

        # Heartbeat was written
        from foreman import paths, store
        hb = store.read_json(paths.watch_heartbeat_file())
        self.assertIsNotNone(hb)
        self.assertEqual(hb["pid"], os.getpid())
        self.assertIn("last_run_at", hb)
        self.assertEqual(hb["interval_s"], 60)

    def test_terminal_gone(self):
        from foreman import sessions, watch, wake

        # Session with handle "fake-dead" not in fake transport
        fake_state = os.path.join(self.fake_transport, "terminals.json")
        with open(fake_state, "w") as f:
            json.dump([], f)

        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": "fake-dead"},
                                    state="busy", title="s-dead")

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()  # first miss: not yet gone (two consecutive passes are required)
            self.assertEqual(mock_wake.call_count, 0)
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "terminal gone: s-dead")

        updated = sessions.load_session(s["sid"])
        self.assertEqual(updated["state"], "gone")

    def test_session_stuck(self):
        from foreman import sessions, watch, wake, transports

        fake_t = transports.get_transport("fake")
        handle = fake_t.launch("claude", "haiku", None, self.work, "hi", "t1")

        os.environ["FOREMAN_NOW"] = "2026-11-05T13:30:00Z"
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-busy")
        # last event 1300s ago (> 1200s stuck_after_s)
        sessions.update_session(s["sid"], last_event_at="2026-11-05T13:08:00Z")

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "session stuck: s-busy")

    def test_session_waiting_too_long(self):
        from foreman import sessions, watch, wake, transports

        fake_t = transports.get_transport("fake")
        handle = fake_t.launch("claude", "haiku", None, self.work, "hi", "t1")

        os.environ["FOREMAN_NOW"] = "2026-11-05T13:30:00Z"
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="waiting_input", title="s-wait")
        # state_since 1000s ago (> 900s waiting_after_s)
        sessions.update_session(s["sid"], state_since="2026-11-05T13:13:00Z")

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "session waiting too long: s-wait")

    def test_context_thresholds(self):
        from foreman import sessions, watch, wake, transports

        fake_t = transports.get_transport("fake")
        handle = fake_t.launch("claude", "haiku", None, self.work, "hi", "t1")

        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-ctx")
        sessions.update_session(s["sid"], context_tokens=160000)

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "session context soft threshold reached: s-ctx (160000 tokens)")

        # crossing hard threshold (250000)
        sessions.update_session(s["sid"], context_tokens=260000)
        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "session context hard threshold reached: s-ctx (260000 tokens)")

    def test_limits_crossing_thresholds_and_dedupe(self):
        from foreman import limits, watch, wake, notify

        # Set limit >= 70 (warn)
        limits.record_reading("claude", {
            "seven_day": {"used_pct": 75, "resets_at": "2026-11-12T12:00:00Z"},
        })

        with patch("foreman.wake.wake") as mock_wake, \
             patch("foreman.notify.notify") as mock_notify:
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 1)
            self.assertEqual(mock_notify.call_count, 1)

            # Second pass: condition deduplicated, 0 new calls
            mock_wake.reset_mock()
            mock_notify.reset_mock()
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)
            self.assertEqual(mock_notify.call_count, 0)

        # Clear condition (limit drops to 50%)
        limits.record_reading("claude", {
            "seven_day": {"used_pct": 50, "resets_at": "2026-11-12T12:00:00Z"},
        })
        watch.run_pass()

        # Re-cross to 75% -> triggers again!
        limits.record_reading("claude", {
            "seven_day": {"used_pct": 75, "resets_at": "2026-11-12T12:00:00Z"},
        })
        with patch("foreman.wake.wake") as mock_wake, \
             patch("foreman.notify.notify") as mock_notify:
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 1)
            self.assertEqual(mock_notify.call_count, 1)

    def test_delivery_for_idle_sessions(self):
        from foreman import sessions, watch, delivery, transports

        fake_t = transports.get_transport("fake")
        handle = fake_t.launch("claude", "haiku", None, self.work, "hi", "t1")

        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="idle", title="s-idle")
        sessions.inbox_enqueue(s["sid"], "hello queued message")

        with patch("foreman.delivery.deliver_pending") as mock_deliver:
            watch.run_pass()
            mock_deliver.assert_called_once_with(s["sid"])

    def test_weekly_lessons_proposal(self):
        from foreman import paths, watch, wake

        lpath = paths.lessons_file("demo")
        with open(lpath, "w") as f:
            f.write("# Rules\n- Always test first\n")

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "propose project-rule changes via PR")

            # Same week, no change -> no wake
            mock_wake.reset_mock()
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)

    def test_cli_watch_run_and_list(self):
        # 1. Before any run, watch list shows NOT RUNNING
        code, out, err = run_cli("watch", "list")
        self.assertEqual(code, 0)
        self.assertIn("NOT RUNNING", out)

        # 2. Run one pass via watch run
        code, out, err = run_cli("watch", "run")
        self.assertEqual(code, 0)

        # 3. Now watch list shows RUNNING
        code, out, err = run_cli("watch", "list")
        self.assertEqual(code, 0)
        self.assertIn("RUNNING", out)
        self.assertIn("Limits:", out)

    def test_limit_stop_wind_down_message_queued_for_workers_only(self):
        from foreman import limits, sessions, watch, transports

        fake_t = transports.get_transport("fake")
        h_orch = fake_t.launch("claude", "haiku", None, self.work, "orch", "t-orch")
        h_worker = fake_t.launch("claude", "haiku", None, self.work, "worker", "t-worker")

        s_orch = sessions.create_session("demo", "claude", role="orchestrator",
                                         transport={"kind": "fake", "handle": h_orch},
                                         state="busy", title="orch")
        s_worker = sessions.create_session("demo", "claude", role="worker",
                                           transport={"kind": "fake", "handle": h_worker},
                                           state="busy", title="worker")

        # Claude crosses 90%
        limits.record_reading("claude", {
            "seven_day": {"used_pct": 92, "resets_at": "2026-11-12T12:00:00Z"},
        })

        watch.run_pass()

        # Worker session received wind-down message in inbox
        pending_worker = sessions.inbox_all(s_worker["sid"])
        self.assertEqual(len(pending_worker), 1)
        self.assertIn("Лимит claude ≥90%: wind down and hand off at your next idle point", pending_worker[0]["text"])

        # Orchestrator is told (a wake about the limit) but is NOT wound down
        pending_orch = sessions.inbox_all(s_orch["sid"])
        self.assertTrue(pending_orch)
        for m in pending_orch:
            self.assertNotIn("wind down", m["text"])

        # Second pass does not queue duplicate message
        watch.run_pass()
        self.assertEqual(len(sessions.inbox_all(s_worker["sid"])), 1)

    def test_lifecycle_closed_unwatched_and_rotated_watched_for_gone(self):
        from foreman import sessions, watch, wake

        # 1. Closed session is completely unwatched
        s_closed = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": "missing-closed"},
                                           state="idle", title="closed-sess")
        sessions.update_session(s_closed["sid"], lifecycle="closed")

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)

        # 2. Rotated session is watched for gone, but never marked closed
        s_rotated = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": "missing-rotated"},
                                            state="idle", title="rotated-sess")
        sessions.update_session(s_rotated["sid"], lifecycle="rotated")

        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "terminal gone: rotated-sess")

        updated = sessions.load_session(s_rotated["sid"])
        self.assertEqual(updated["state"], "gone")
        # Lifecycle is still "rotated", NEVER marked "closed"
        self.assertEqual(updated.get("lifecycle"), "rotated")

    def test_watch_list_shows_last_hook_event_per_agent(self):
        from foreman import paths, sessions, store

        os.environ["FOREMAN_NOW"] = "2026-11-05T13:30:00Z"
        s = sessions.create_session("demo", "claude", state="idle", title="c1")
        sessions.update_session(s["sid"], last_event_at="2026-11-05T13:25:00Z")  # 5m ago

        # Antigravity event in events.jsonl
        store.append_jsonl(paths.events_file(), {
            "ts": "2026-11-05T13:20:00Z",  # 10m ago
            "agent": "antigravity",
            "event": "tool_call",
        })

        code, out, err = run_cli("watch", "list")
        self.assertEqual(code, 0)
        self.assertIn("Hook events:", out)
        self.assertIn("Claude: 5m ago", out)
        self.assertIn("Antigravity: 10m ago", out)

    def test_delivery_claims_with_inbox_claim_and_never_delivers_twice(self):
        from foreman import delivery, sessions, transports

        fake_t = transports.get_transport("fake")
        handle = fake_t.launch("claude", "haiku", None, self.work, "hi", "t-deliv")

        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="idle", title="s-deliver")
        sessions.inbox_enqueue(s["sid"], "msg 1")
        sessions.inbox_enqueue(s["sid"], "msg 2")

        # First delivery succeeds
        res1 = delivery.deliver_pending(s["sid"])
        self.assertEqual(res1, "delivered")

        # Second delivery has no pending messages (already claimed!)
        res2 = delivery.deliver_pending(s["sid"])
        self.assertEqual(res2, "nothing")

        # All messages have delivered_at stamped
        all_msgs = sessions.inbox_all(s["sid"])
        self.assertEqual(len(all_msgs), 2)
        self.assertTrue(all(m.get("delivered_at") for m in all_msgs))



