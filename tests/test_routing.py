import os
import unittest

from tests.helpers import ForemanTestCase

OK = {"claude": "ok", "antigravity": "ok"}


def lv(claude="ok", antigravity="ok", **more):
    d = {"claude": claude, "antigravity": antigravity}
    d.update(more)
    return d


class ChooseTest(ForemanTestCase):
    def choose(self, *a, **kw):
        from foreman import routing
        return routing.choose(*a, **kw)

    def test_defaults_when_everything_is_ok(self):
        expect = {
            "routine": ("claude", "sonnet", None),
            "research": ("antigravity", "gemini-3.8-flash-medium", None),
            "design": ("antigravity", "gemini-4-argon-medium", None),
            "review": ("claude", "opus", None),
            "hard": ("claude", "opus", None),
            "orchestrator": ("claude", "sonnet", None),
        }
        for kind, want in expect.items():
            agent, model, effort, note = self.choose(kind, OK)
            self.assertEqual((agent, model, effort), want, kind)
            self.assertEqual(note, "", kind)

    def test_unknown_kind(self):
        from foreman import routing
        with self.assertRaises(routing.RoutingError):
            self.choose("nonsense", OK)

    def test_second_choices(self):
        self.assertEqual(self.choose("routine", lv(claude="warn"))[:3], ("antigravity", "gemini-3.8-flash-high", None))
        self.assertEqual(self.choose("research", lv(antigravity="stop"))[:3], ("claude", "sonnet", None))
        self.assertEqual(self.choose("review", lv(claude="stop"))[:3], ("antigravity", "claude-opus-5-5-high", None))
        self.assertEqual(self.choose("hard", lv(claude="stop"))[:3], ("antigravity", "claude-opus-5-5-high", None))

    def test_warn_prefers_the_other_provider_and_says_so(self):
        agent, model, _, note = self.choose("routine", lv(claude="warn"))
        self.assertEqual(agent, "antigravity")
        self.assertIn("claude", note)
        self.assertIn("warn", note)
        self.assertIn("antigravity", note)

    def test_warn_without_alternative_keeps_going_with_a_note(self):
        agent, model, _, note = self.choose("design", lv(antigravity="warn"))
        self.assertEqual((agent, model), ("antigravity", "gemini-4-argon-medium"))
        self.assertIn("warn", note)

    def test_both_warn_picks_the_first_choice(self):
        agent, _, _, note = self.choose("routine", lv(claude="warn", antigravity="warn"))
        self.assertEqual(agent, "claude")
        self.assertIn("warn", note)

    def test_warn_is_worse_than_unknown(self):
        agent, _, _, note = self.choose("routine", lv(claude="warn", antigravity="unknown"))
        self.assertEqual(agent, "antigravity")

    def test_stop_is_never_chosen(self):
        from foreman import routing
        self.assertEqual(self.choose("routine", lv(claude="stop"))[0], "antigravity")
        with self.assertRaises(routing.RoutingError) as cm:
            self.choose("routine", lv(claude="stop", antigravity="stop"))
        self.assertIn("stop", str(cm.exception))
        with self.assertRaises(routing.RoutingError):
            self.choose("design", lv(antigravity="stop"))   # no alternative
        with self.assertRaises(routing.RoutingError):
            self.choose("orchestrator", lv(claude="stop"))

    def test_unknown_is_allowed_with_a_note(self):
        agent, _, _, note = self.choose("routine", lv(claude="unknown"))
        self.assertEqual(agent, "claude")
        self.assertIn("unknown", note)

    def test_missing_provider_level_counts_as_unknown(self):
        agent, _, _, note = self.choose("routine", {})
        self.assertEqual(agent, "claude")
        self.assertIn("unknown", note)

    def test_reading_older_than_window_reset_counts_as_unknown(self):
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        stale_stop = {
            "used_pct": 95,
            "resets_at": "2026-11-05T12:00:00Z",
            "observed_at": "2026-11-05T11:00:00Z",
        }
        agent, _, _, note = self.choose("routine", {"claude": stale_stop, "antigravity": "ok"})
        self.assertEqual(agent, "claude")
        self.assertIn("unknown", note)
        fresh_stop = {
            "used_pct": 95,
            "resets_at": "2026-11-05T14:00:00Z",
            "observed_at": "2026-11-05T12:30:00Z",
        }
        agent, _, _, _ = self.choose("routine", {"claude": fresh_stop, "antigravity": "ok"})
        self.assertEqual(agent, "antigravity")

    def test_explicit_agent_and_model(self):
        self.assertEqual(self.choose("routine", OK, preferred=("antigravity", "gemini-4-argon-medium", "x"))[:3],
                         ("antigravity", "gemini-4-argon-medium", "x"))
        self.assertEqual(self.choose("routine", OK, preferred={"agent": "codex", "model": "gpt"})[:3],
                         ("codex", "gpt", None))

    def test_explicit_agent_name_takes_the_model_from_the_table(self):
        self.assertEqual(self.choose("review", OK, preferred="antigravity")[:3],
                         ("antigravity", "claude-opus-5-5-high", None))
        self.assertEqual(self.choose("routine", OK, preferred=("claude", None, None))[:3], ("claude", "sonnet", None))
        # no table entry for that agent: its own default model
        self.assertEqual(self.choose("design", OK, preferred="claude")[:3], ("claude", None, None))

    def test_explicit_choice_at_stop_is_refused_even_with_alternatives(self):
        from foreman import routing
        with self.assertRaises(routing.RoutingError) as cm:
            self.choose("routine", lv(claude="stop"), preferred="claude")
        self.assertEqual(cm.exception.providers, ["claude"])

    def test_explicit_choice_at_warn_is_kept_with_a_note(self):
        agent, _, _, note = self.choose("routine", lv(claude="warn"), preferred="claude")
        self.assertEqual(agent, "claude")
        self.assertIn("warn", note)

    def test_antigravity_served_claude_counts_against_antigravity(self):
        from foreman import routing
        self.assertEqual(routing.provider_of("antigravity", "claude-opus-5-5-high"), "antigravity")
        self.assertEqual(routing.provider_of("claude", "opus"), "claude")
        with self.assertRaises(routing.RoutingError):
            self.choose("review", lv(claude="stop", antigravity="stop"))
        # claude ok, antigravity stop: the antigravity-served opus is not chosen, claude opus is
        self.assertEqual(self.choose("review", lv(antigravity="stop"))[:2], ("claude", "opus"))
        with self.assertRaises(routing.RoutingError):
            self.choose("review", lv(antigravity="stop"), preferred=("antigravity", "claude-opus-5-5-high", None))

    def test_config_overrides_defaults(self):
        self.write_config({"routing": {"routine": [{"agent": "antigravity", "model": "gemini-x", "effort": "low"},
                                                   ["claude", "haiku"]]}})
        self.assertEqual(self.choose("routine", OK)[:3], ("antigravity", "gemini-x", "low"))
        self.assertEqual(self.choose("routine", lv(antigravity="warn"))[:3], ("claude", "haiku", None))
        # other kinds keep their defaults
        self.assertEqual(self.choose("review", OK)[:2], ("claude", "opus"))

    def test_config_can_define_a_new_kind_and_bad_rows_are_ignored(self):
        self.write_config({"routing": {"hard": ["junk", {"agent": "claude", "model": "opus"}, 7]}})
        self.assertEqual(self.choose("hard", OK)[:2], ("claude", "opus"))

    def test_codex_level_is_honoured(self):
        from foreman import routing
        with self.assertRaises(routing.RoutingError):
            self.choose("routine", lv(codex="stop"), preferred="codex")


class ColdCacheTest(ForemanTestCase):
    def warn(self, rec):
        from foreman import routing
        return routing.cold_cache_warning(rec)

    def test_cold_when_rotated_and_older_than_55_minutes(self):
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        text = self.warn({"sid": "s-aaaaaa", "state": "idle", "lifecycle": "rotated",
                          "last_event_at": "2026-11-05T11:50:00Z"})
        self.assertIn("cold", text)
        self.assertIn("s-aaaaaa", text)

    def test_warm_inside_55_minutes(self):
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        self.assertIsNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": "rotated",
                                     "last_event_at": "2026-11-05T12:10:00Z"}))
        self.assertIsNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": "rotated",
                                     "last_event_at": "2026-11-05T12:06:00Z"}))

    def test_boundary(self):
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        self.assertIsNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": "rotated",
                                     "last_event_at": "2026-11-05T12:05:00Z"}))
        self.assertIsNotNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": "rotated",
                                        "last_event_at": "2026-11-05T12:04:59Z"}))

    def test_only_rotated_sessions(self):
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        for lifecycle in ("active", "closed"):
            self.assertIsNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": lifecycle,
                                         "last_event_at": "2026-11-05T01:00:00Z"}))

    def test_falls_back_to_state_since_then_to_nothing(self):
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        self.assertIsNotNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": "rotated",
                                        "last_event_at": None, "state_since": "2026-11-05T01:00:00Z"}))
        self.assertIsNone(self.warn({"sid": "s-a", "state": "idle", "lifecycle": "rotated"}))
        self.assertIsNone(self.warn(None))


if __name__ == "__main__":
    unittest.main()
