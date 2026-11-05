import json
import os
import unittest
from unittest.mock import patch, MagicMock

from tests.helpers import ForemanTestCase, run_cli


class WatchTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
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

