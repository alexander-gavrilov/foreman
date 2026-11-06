"""Fix round 9: the digest's limits line against `foreman limits` (agy's past reset_time)."""
from tests.test_hooks import HookCase


class LimitsLineTest(HookCase):
    """Live: the digest said `agy wk ? · 5h ? (24m)` while `foreman limits` showed wk 27%. agy sends a `reset_time`
    that is already in the past (with reset_in_seconds 0) for a window it has not restarted; foreman took that for
    'the reading belongs to a window that is over' and showed '?'."""
    AGY = {"quota": {"gemini-weekly": {"remaining_fraction": 0.73, "reset_time": "2026-10-25T19:28:10Z",
                                       "reset_in_seconds": 0},
                     "gemini-5h": {"remaining_fraction": 0.9, "reset_time": "2026-10-24T19:45:05Z",
                                   "reset_in_seconds": 0}}}

    def test_a_past_reset_time_of_a_fresh_agy_reading_does_not_make_it_unknown(self):
        from foreman import limits
        limits.record_reading("antigravity", limits.parse_antigravity_statusline(self.AGY))
        self.assertIn("agy wk 27% · 5h 10%", limits.summary_line())

    def test_the_line_and_the_table_agree(self):
        from foreman import limits
        limits.record_reading("antigravity", limits.parse_antigravity_statusline(self.AGY))
        weekly = limits.provider_windows("antigravity")["weekly"]
        self.assertEqual(limits.fresh_pct("weekly", weekly), 27)
        self.assertEqual(limits.window_level("weekly", weekly), "ok")

    def test_a_future_reset_time_is_kept_and_a_really_stale_reading_is_still_unknown(self):
        from foreman import limits
        future = {"quota": {"gemini-weekly": {"remaining_fraction": 0.5, "reset_time": "2099-01-01T00:00:00Z",
                                              "reset_in_seconds": 99999}}}
        windows = limits.parse_antigravity_statusline(future)
        self.assertEqual(windows["weekly"]["resets_at"], "2099-01-01T00:00:00Z")
        old = {"weekly": {"used_pct": 27, "resets_at": None, "observed_at": "2026-10-01T00:00:00Z"}}
        self.assertIsNone(limits.fresh_pct("weekly", old["weekly"]))

    def test_a_reading_with_a_past_reset_time_and_time_left_is_not_changed(self):
        from foreman import limits
        past = {"quota": {"gemini-weekly": {"remaining_fraction": 0.5, "reset_time": "2020-01-01T00:00:00Z",
                                            "reset_in_seconds": 3600}}}
        self.assertEqual(limits.parse_antigravity_statusline(past)["weekly"]["resets_at"], "2020-01-01T00:00:00Z")
