"""Review 3a fixes: watch (F1 gone detection, F2 stale limits) and janitor (F4)."""
import json
import os
from unittest.mock import patch

from tests.helpers import ForemanTestCase


def run_main(*args):
    import contextlib
    import io
    from foreman import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


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


class StaleLimitsTest(ForemanTestCase):
    NOW = "2026-11-05T12:00:00Z"

    def setUp(self):
        super().setUp()
        self.init_project("demo")
        os.environ["FOREMAN_NOW"] = self.NOW

    def _stale_claude(self):
        from foreman import limits
        limits.record_reading("claude", {
            "five_hour": {"used_pct": 95, "resets_at": "2026-11-04T10:00:00Z"},
        }, observed_at="2026-11-04T09:00:00Z")

    def test_levels_treats_window_past_resets_at_as_unknown(self):
        from foreman import limits
        self._stale_claude()
        self.assertEqual(limits.levels()["claude"], "unknown")

    def test_levels_without_resets_at_uses_window_length_from_observed_at(self):
        from foreman import limits
        limits.record_reading("claude", {"five_hour": {"used_pct": 95, "resets_at": None}},
                              observed_at="2026-11-05T06:00:00Z")  # 6h > 5h window
        self.assertEqual(limits.levels()["claude"], "unknown")
        limits.record_reading("claude", {"five_hour": {"used_pct": 95, "resets_at": None}},
                              observed_at="2026-11-05T10:00:00Z")  # 2h < 5h window
        self.assertEqual(limits.levels()["claude"], "stop")

    def test_one_stale_window_does_not_hide_a_fresh_one(self):
        from foreman import limits
        limits.record_reading("claude", {
            "five_hour": {"used_pct": 95, "resets_at": "2026-11-04T10:00:00Z"},
            "seven_day": {"used_pct": 75, "resets_at": "2026-11-10T10:00:00Z"},
        })
        self.assertEqual(limits.levels()["claude"], "warn")

    def test_summary_line_shows_stale_window_as_unknown(self):
        from foreman import limits
        limits.record_reading("claude", {
            "five_hour": {"used_pct": 95, "resets_at": "2026-11-04T10:00:00Z"},
            "seven_day": {"used_pct": 20, "resets_at": "2026-11-10T10:00:00Z"},
        }, observed_at="2026-11-05T11:56:00Z")
        line = limits.summary_line()
        self.assertIn("wk 20%", line)
        self.assertIn("5h ?", line)
        self.assertNotIn("95", line)

    def test_open_is_not_refused_on_a_stale_stop_reading(self):
        self._stale_claude()
        code, out, err = run_main("open", "--project", "demo", "--agent", "claude", "--purpose", "x",
                                  "--title", "fm-x", "--transport", "fake", "--prompt", "hi")
        self.assertEqual(code, 0, err)

    def test_watch_ignores_stale_reading(self):
        from foreman import sessions, watch, transports
        self._stale_claude()
        h = transports.get_transport("fake").launch("claude", "haiku", None, self.work, "hi", "t")
        w = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": h}, state="idle",
                                    title="w1")
        with patch("foreman.wake.wake") as mock_wake, patch("foreman.notify.notify") as mock_notify:
            watch.run_pass()
        self.assertEqual(mock_wake.call_count, 0)
        self.assertEqual(mock_notify.call_count, 0)
        self.assertEqual(sessions.inbox_pending(w["sid"]), [])


class JanitorReuseTest(ForemanTestCase):
    T0 = 1793700000  # a fixed "now" (epoch seconds)

    def setUp(self):
        super().setUp()
        from foreman import paths
        self.init_project("demo")
        self.folder = os.path.join(paths.agent_work_dir("demo"), "2026-10-23-reused")
        os.makedirs(self.folder)
        self.file = os.path.join(self.folder, "draft.txt")
        with open(self.file, "w") as f:
            f.write("x")

    def _now(self, epoch):
        from datetime import datetime, timezone
        os.environ["FOREMAN_NOW"] = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _touch(self, epoch):
        for p in (self.file, self.folder):
            os.utime(p, (epoch, epoch))

    def _announce_at_13_5_days(self):
        from foreman import janitor
        self._now(self.T0)
        self._touch(self.T0 - int(13.5 * 86400))
        state = {}
        with patch("foreman.notify.notify") as n:
            janitor.janitor_pass(state)
        self.assertEqual(n.call_count, 1)
        self.assertIn(self.folder, state["janitor"])
        return state

    def test_reused_folder_drops_its_announcement(self):
        from foreman import janitor
        state = self._announce_at_13_5_days()
        self._now(self.T0 + 86400)
        self._touch(self.T0 + 86400)  # used again
        with patch("foreman.notify.notify"):
            janitor.janitor_pass(state)
        self.assertNotIn(self.folder, state["janitor"])
        # 14.1 days after the new use: announced afresh, not deleted without a new listing
        self._now(self.T0 + 86400 + int(14.1 * 86400))
        with patch("foreman.notify.notify") as n:
            janitor.janitor_pass(state)
        self.assertTrue(os.path.exists(self.folder))
        self.assertEqual(n.call_count, 1)

    def test_touched_since_announcement_is_not_deleted_even_without_an_intermediate_pass(self):
        from foreman import janitor
        state = self._announce_at_13_5_days()
        self._touch(self.T0 + 86400)  # used again, janitor did not run meanwhile
        self._now(self.T0 + 86400 + int(14.1 * 86400))
        with patch("foreman.notify.notify") as n:
            janitor.janitor_pass(state)
        self.assertTrue(os.path.exists(self.folder))
        self.assertEqual(n.call_count, 1)  # re-announced

    def test_untouched_announced_folder_is_deleted_at_ttl(self):
        from foreman import janitor
        state = self._announce_at_13_5_days()
        self._now(self.T0 + 2 * 86400)
        with patch("foreman.notify.notify"):
            janitor.janitor_pass(state)
        self.assertFalse(os.path.exists(self.folder))
