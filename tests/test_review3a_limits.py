"""Review 3a F10: Antigravity's 3p (Claude etc.) windows apply to 3p models only."""
import json
import os
from unittest.mock import patch

from tests.helpers import ForemanTestCase, run_cli
from tests.test_review3a_watch import run_main


class ThirdPartyWindowsTest(ForemanTestCase):
    NOW = "2026-11-05T12:00:00Z"
    FUTURE = "2026-11-05T17:00:00Z"

    def setUp(self):
        super().setUp()
        os.environ["FOREMAN_NOW"] = self.NOW
        self.init_project("demo")
        from foreman import limits
        limits.record_reading("antigravity", {
            "five_hour": {"used_pct": 10, "resets_at": self.FUTURE},
            "weekly": {"used_pct": 20, "resets_at": "2026-11-12T12:00:00Z"},
            "3p_five_hour": {"used_pct": 95, "resets_at": self.FUTURE},
        })

    def test_levels_separate_gemini_from_3p(self):
        from foreman import limits
        lv = limits.levels()
        self.assertEqual(lv["antigravity"], "ok")
        self.assertEqual(lv["antigravity-3p"], "stop")

    def test_no_3p_key_without_3p_windows(self):
        from foreman import limits, paths
        os.remove(paths.limits_file("antigravity"))
        limits.record_reading("antigravity", {"five_hour": {"used_pct": 10, "resets_at": self.FUTURE}})
        self.assertNotIn("antigravity-3p", limits.levels())

    def test_gemini_open_is_allowed_and_3p_open_is_refused(self):
        code, out, err = run_main("open", "--project", "demo", "--agent", "antigravity", "--model",
                                  "gemini-3.8-flash-high", "--purpose", "x", "--title", "g1",
                                  "--transport", "fake", "--prompt", "hi")
        self.assertEqual(code, 0, err)
        code, out, err = run_main("open", "--project", "demo", "--agent", "antigravity", "--model",
                                  "claude-opus-5-5-high", "--purpose", "x", "--title", "c1",
                                  "--transport", "fake", "--prompt", "hi")
        self.assertEqual(code, 5, err)

    def test_routing_skips_only_the_3p_candidate(self):
        from foreman import limits, routing
        lv = dict(limits.levels(), claude="stop")
        # 'review' = claude opus, then antigravity claude (3p, at stop): nothing left
        with self.assertRaises(routing.RoutingError):
            routing.choose("review", lv)
        # 'research' = antigravity gemini first: usable although 3p is at stop
        agent, model, _, _ = routing.choose("research", lv)
        self.assertEqual((agent, model), ("antigravity", "gemini-3.8-flash-medium"))

    def test_watch_winds_down_only_3p_workers(self):
        from foreman import sessions, watch, transports
        t = transports.get_transport("fake")
        gem = sessions.create_session("demo", "antigravity", model="gemini-3.8-flash-high", state="idle",
                                      title="gem", transport={"kind": "fake", "handle": t.launch(
                                          "antigravity", "g", None, self.work, "p", "gem")})
        cl3p = sessions.create_session("demo", "antigravity", model="claude-opus-5-5-high", state="idle",
                                       title="cl3p", transport={"kind": "fake", "handle": t.launch(
                                           "antigravity", "c", None, self.work, "p", "cl3p")})
        with patch("foreman.wake.wake"), patch("foreman.notify.notify"):
            watch.run_pass()
        self.assertEqual(sessions.inbox_all(gem["sid"]), [])  # (idle workers get queued mail delivered at once)
        self.assertEqual(len(sessions.inbox_all(cl3p["sid"])), 1)

    def test_limits_command_shows_3p_as_a_separate_block(self):
        code, out, err = run_main("limits")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        i = [n for n, l in enumerate(lines) if l.startswith("Antigravity 3p")][0]
        self.assertIn("3p_five_hour", "\n".join(lines[i:]))
        self.assertNotIn("3p_five_hour", "\n".join(lines[:i]))
        code, out, err = run_main("limits", "--json")
        data = json.loads(out)
        self.assertEqual(data["antigravity"]["level"], "ok")
        self.assertNotIn("3p_five_hour", data["antigravity"]["windows"])
        self.assertEqual(data["antigravity-3p"]["level"], "stop")
        self.assertIn("3p_five_hour", data["antigravity-3p"]["windows"])
