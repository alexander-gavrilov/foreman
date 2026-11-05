"""Spec §8.8 — a limit at >=70% steers new sessions to the other provider; at >=90% no new sessions start there.
An empty reading never hides the last known value.

Readings come in the only way they exist in real life: the agents' statusline JSON piped through
`foreman tap statusline`. Sessions are then opened through the real `foreman open` (fake terminal)."""
import json
import unittest

from tests.acceptance.common import AcceptanceCase


class LimitsTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slug, self.path = self.make_project()

    def open_no_preference(self, kind="routine", title="w"):
        code, out, err = self.fm("open", "--project", self.slug, "--kind", kind, "--purpose", "p", "--title", title,
                                 "--prompt", "go", "--transport", "fake", "--json")
        return code, (json.loads(out) if code == 0 else None), err

    def agy_statusline(self, five_hour=None, weekly=None):
        quota = {}
        if five_hour is not None:
            quota["gemini-5h"] = {"remaining_fraction": round(1 - five_hour / 100.0, 4), "reset_time": "2026-11-09T00:00:00Z"}
        if weekly is not None:
            quota["gemini-weekly"] = {"remaining_fraction": round(1 - weekly / 100.0, 4), "reset_time": "2026-11-09T00:00:00Z"}
        return {"quota": quota}

    def limits(self):
        return self.fm_json("limits", "--json")

    # --- 70% -----------------------------------------------------------------------------------------------

    def test_below_70_the_default_provider_is_used(self):
        self.tap_claude(self.claude_statusline(weekly=40, five_hour=20))
        code, rec, err = self.open_no_preference()
        self.assertEqual((code, rec["agent"]), (0, "claude"), err)

    def test_at_70_percent_new_sessions_go_to_the_other_provider_and_say_so(self):
        self.tap_claude(self.claude_statusline(weekly=70, five_hour=10))
        code, rec, err = self.open_no_preference()
        self.assertEqual((code, rec["agent"]), (0, "antigravity"), err)
        self.assertIn("claude is at warn", rec["note"])
        self.assertIn("preferring antigravity", rec["note"])
        self.assertEqual(self.limits()["claude"]["level"], "warn")

    def test_a_five_hour_window_alone_also_steers(self):
        self.tap_claude(self.claude_statusline(weekly=10, five_hour=85))
        code, rec, _ = self.open_no_preference()
        self.assertEqual(rec["agent"], "antigravity")

    # --- 90% -----------------------------------------------------------------------------------------------

    def test_at_90_percent_no_new_session_starts_on_that_provider_even_when_named(self):
        self.tap_claude(self.claude_statusline(weekly=90, five_hour=10))
        code, out, err = self.fm("open", "--project", self.slug, "--agent", "claude", "--purpose", "p",
                                 "--prompt", "go", "--transport", "fake")
        self.assertEqual(code, 5)
        self.assertIn("claude is at its limit", err)
        # nothing was launched on a terminal and no session was registered
        self.assertEqual(self.terminal().list(), [])
        code, rec, _ = self.open_no_preference()
        self.assertEqual((code, rec["agent"]), (0, "antigravity"))
        self.assertIn("claude at stop: skipped", rec["note"])

    def test_a_task_only_one_provider_can_serve_is_refused_when_it_is_at_90(self):
        self.tap_antigravity(self.agy_statusline(five_hour=40, weekly=92))
        code, rec, err = self.open_no_preference(kind="design")  # design routes to Antigravity only
        self.assertEqual(code, 5, err)
        self.assertIn("antigravity", err)

    def test_both_providers_at_90_refuse_everything(self):
        self.tap_claude(self.claude_statusline(weekly=95))
        self.tap_antigravity(self.agy_statusline(weekly=91))
        code, rec, err = self.open_no_preference()
        self.assertEqual(code, 5)
        self.assertIn("at its limit", err)

    def test_the_orchestrator_cannot_be_started_on_a_provider_at_90(self):
        self.tap_claude(self.claude_statusline(weekly=94))
        code, out, err = self.fm("orchestrator", "start", "--project", self.slug, "--agent", "claude",
                                 "--transport", "fake")
        self.assertEqual(code, 5)

    # --- empty readings --------------------------------------------------------------------------------------

    def test_an_empty_reading_never_hides_the_last_known_value(self):
        self.tap_claude(self.claude_statusline(weekly=72, five_hour=30))
        self.advance(minutes=30)
        for empty in ({}, {"rate_limits": {}}, {"rate_limits": {"seven_day": {"used_percentage": None}}},
                      {"rate_limits": {"five_hour": None, "seven_day": None}}):
            self.tap_claude(empty)
        self.code_garbage_tap()
        data = self.limits()["claude"]
        self.assertEqual(data["windows"]["seven_day"]["used_pct"], 72)
        self.assertEqual(data["windows"]["five_hour"]["used_pct"], 30)
        self.assertEqual(data["windows"]["seven_day"]["age"], "30m", "the age keeps growing from the last good reading")
        self.assertEqual(data["level"], "warn")
        # decisions still use the last good value
        code, rec, _ = self.open_no_preference()
        self.assertEqual(rec["agent"], "antigravity")
        self.assertIn("72%", self.fm_ok("limits", "--line"))

    def code_garbage_tap(self):
        code, out, err = self.fm("tap", "statusline", "--agent", "claude", "--", "cat", stdin="not json {{")
        self.assertEqual(code, 0)

    def test_antigravity_quota_null_does_not_overwrite_the_last_reading(self):
        self.tap_antigravity(self.agy_statusline(five_hour=55, weekly=33))
        self.advance(hours=1)
        self.tap_antigravity({"quota": None})
        self.tap_antigravity({"quota": {"gemini-5h": {"remaining_fraction": None, "reset_time": None}}})
        windows = self.limits()["antigravity"]["windows"]
        self.assertEqual((windows["five_hour"]["used_pct"], windows["weekly"]["used_pct"]), (55, 33))
        self.assertEqual(windows["five_hour"]["age"], "1h")

    def test_no_reading_at_all_is_unknown_never_zero(self):
        data = self.limits()
        self.assertEqual((data["claude"]["level"], data["claude"]["windows"]), ("unknown", {}))
        self.assertIn("(no readings)", self.fm_ok("limits"))
        code, rec, _ = self.open_no_preference()
        self.assertEqual(code, 0)
        self.assertIn("limits unknown", rec["note"])
        self.assertEqual(self.fm_ok("limits", "--line"), "Claude ? | agy ?")

    def test_the_statusline_itself_is_passed_through_untouched(self):
        payload = self.claude_statusline(weekly=50)
        self.assertEqual(self.tap_claude(payload), json.dumps(payload))

    # --- architecture §6: a stale reading -------------------------------------------------------------------------

    # PRODUCT BUG: limits.levels() (used by `foreman open`) classifies by used_pct only and ignores resets_at, so a
    # 95% reading stays "stop" for ever, although architecture §6 and spec §4.4 say a reading older than its
    # window's reset counts as unknown (routing.window_level already implements that; levels() never calls it).
    @unittest.expectedFailure
    def test_a_reading_older_than_its_window_reset_no_longer_blocks(self):
        """Architecture §6: a reading older than the window's reset counts as unknown, which allows the provider."""
        self.tap_claude(self.claude_statusline(weekly=95))  # resets 3 days after T0
        self.advance(days=4)
        code, out, err = self.fm("open", "--project", self.slug, "--agent", "claude", "--purpose", "p",
                                 "--prompt", "go", "--transport", "fake", "--json")
        self.assertEqual(code, 0, "a 95%% reading from before the window reset must not block forever: %s" % err)
        self.assertIn("limits unknown", json.loads(out)["note"])


if __name__ == "__main__":
    unittest.main()
