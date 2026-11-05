"""Review 3a fixes: watch (F1 gone detection, F2 stale limits) and janitor (F4)."""
import json
import os
from unittest.mock import patch

from tests.helpers import ForemanTestCase


def _set_alive(root, handle, alive):
    path = os.path.join(root, "terminals", handle + ".json")
    with open(path) as f:
        rec = json.load(f)
    rec["alive"] = alive
    with open(path, "w") as f:
        json.dump(rec, f)


class GoneDetectionTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.init_project("demo")

    def _fake_session(self, **kw):
        from foreman import sessions, transports
        handle = transports.get_transport("fake").launch("claude", "haiku", None, self.work, "hi", "t")
        kw.setdefault("title", "w1")
        kw.setdefault("state", "idle")
        return sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle}, **kw)

    def test_failed_list_does_not_mark_sessions_gone(self):
        from foreman import sessions, watch
        s = self._fake_session()
        with patch("foreman.transports.fake.FakeTransport.list", side_effect=RuntimeError("orca restarted")), \
                patch("foreman.wake.wake") as mock_wake:
            for _ in range(3):
                watch.run_pass()
        self.assertEqual(mock_wake.call_count, 0)
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")

    def test_gone_needs_two_consecutive_passes_with_alive_false(self):
        from foreman import sessions, watch
        s = self._fake_session()
        h = s["transport"]["handle"]
        _set_alive(self.fake_transport, h, False)
        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)
            self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")
            watch.run_pass()
            mock_wake.assert_called_once_with("demo", "terminal gone: w1")
        self.assertEqual(sessions.load_session(s["sid"])["state"], "gone")

    def test_missing_from_list_but_alive_is_not_gone(self):
        from foreman import sessions, watch
        s = self._fake_session()
        with patch("foreman.transports.fake.FakeTransport.list", return_value=[]), \
                patch("foreman.wake.wake") as mock_wake:
            for _ in range(3):
                watch.run_pass()
        self.assertEqual(mock_wake.call_count, 0)
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")

    def test_single_missed_pass_then_seen_resets_the_counter(self):
        from foreman import sessions, watch
        s = self._fake_session()
        h = s["transport"]["handle"]
        with patch("foreman.wake.wake") as mock_wake:
            _set_alive(self.fake_transport, h, False)
            watch.run_pass()  # miss 1
            _set_alive(self.fake_transport, h, True)
            watch.run_pass()  # seen again: counter reset
            _set_alive(self.fake_transport, h, False)
            watch.run_pass()  # miss 1 again, not 2
        self.assertEqual(mock_wake.call_count, 0)
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")

    def test_handle_seen_again_clears_gone_and_dedupe(self):
        from foreman import paths, sessions, store, watch
        s = self._fake_session()
        h = s["transport"]["handle"]
        _set_alive(self.fake_transport, h, False)
        with patch("foreman.wake.wake"):
            watch.run_pass()
            watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "gone")
        self.assertIn("gone:" + s["sid"], store.read_json(paths.watch_state_file())["dedupe"])
        _set_alive(self.fake_transport, h, True)
        with patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
        self.assertEqual(mock_wake.call_count, 0)
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")
        self.assertNotIn("gone:" + s["sid"], store.read_json(paths.watch_state_file())["dedupe"])

    def test_failed_list_keeps_orchestrator_findable(self):
        from foreman import watch, wake
        orch = self._fake_session(role="orchestrator", owner="user", title="orch")
        with patch("foreman.transports.fake.FakeTransport.list", side_effect=RuntimeError("boom")):
            watch.run_pass()
        self.assertEqual(wake.find_orchestrator("demo")["sid"], orch["sid"])
