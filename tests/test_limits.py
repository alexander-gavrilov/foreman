import json
import os
import subprocess
import sys
import unittest

from tests.helpers import ForemanTestCase, run_cli


class LimitsTest(ForemanTestCase):
    def test_record_reading_merges_and_preserves_missing(self):
        from foreman import limits, store, paths

        # Initial reading with five_hour and weekly
        r1 = limits.record_reading("claude", {
            "five_hour": {"used_pct": 20, "resets_at": "2026-11-05T17:00:00Z"},
            "seven_day": {"used_pct": 50, "resets_at": "2026-11-12T12:00:00Z"},
        }, observed_at="2026-11-05T12:00:00Z")

        self.assertEqual(r1["five_hour"]["used_pct"], 20)
        self.assertEqual(r1["five_hour"]["observed_at"], "2026-11-05T12:00:00Z")
        self.assertEqual(r1["seven_day"]["used_pct"], 50)

        # Second reading updates five_hour, but seven_day is missing / null
        r2 = limits.record_reading("claude", {
            "five_hour": {"used_pct": 35, "resets_at": "2026-11-05T17:00:00Z"},
            "seven_day": {"used_pct": None, "resets_at": None},
        }, observed_at="2026-11-05T13:00:00Z")

        self.assertEqual(r2["five_hour"]["used_pct"], 35)
        self.assertEqual(r2["five_hour"]["observed_at"], "2026-11-05T13:00:00Z")
        # seven_day preserved last good reading
        self.assertEqual(r2["seven_day"]["used_pct"], 50)
        self.assertEqual(r2["seven_day"]["observed_at"], "2026-11-05T12:00:00Z")

    def test_parse_claude_statusline(self):
        from foreman import limits

        payload = {
            "rate_limits": {
                "five_hour": {"used_percentage": 15.5, "resets_at": 1793530800},
                "seven_day": {"used_percentage": 42.0, "resets_at": 1793998800},
            }
        }
        windows = limits.parse_claude_statusline(payload)
        self.assertEqual(windows["five_hour"]["used_pct"], 16)
        self.assertIn("resets_at", windows["five_hour"])
        self.assertEqual(windows["seven_day"]["used_pct"], 42)

    def test_parse_antigravity_statusline(self):
        from foreman import limits

        # Quota null -> nothing recorded
        self.assertEqual(limits.parse_antigravity_statusline({"quota": None}), {})
        self.assertEqual(limits.parse_antigravity_statusline({}), {})

        payload = {
            "quota": {
                "gemini-5h": {"remaining_fraction": 0.85, "reset_time": "2026-11-05T18:00:00Z"},
                "gemini-weekly": {"remaining_fraction": 0.40, "reset_time": "2026-11-12T18:00:00Z"},
                "3p-5h": {"remaining_fraction": 0.90, "reset_time": "2026-11-05T19:00:00Z"},
                "3p-weekly": {"remaining_fraction": 0.75, "reset_time": "2026-11-12T19:00:00Z"},
            }
        }
        windows = limits.parse_antigravity_statusline(payload)
        self.assertEqual(windows["five_hour"]["used_pct"], 15)
        self.assertEqual(windows["five_hour"]["resets_at"], "2026-11-05T18:00:00Z")
        self.assertEqual(windows["weekly"]["used_pct"], 60)
        self.assertEqual(windows["3p_five_hour"]["used_pct"], 10)
        self.assertEqual(windows["3p_weekly"]["used_pct"], 25)

    def test_import_legacy_snapshots(self):
        from foreman import limits

        # Set up legacy files in fake home
        agent_limits_dir = os.path.join(self.home, ".cache", "agent-limits")
        os.makedirs(agent_limits_dir, exist_ok=True)

        claude_legacy = {
            "at": 1793519049,
            "rate_limits": {
                "five_hour": {"used_percentage": 8, "resets_at": 1793530800},
                "seven_day": {"used_percentage": 3, "resets_at": 1793998800},
            }
        }
        with open(os.path.join(agent_limits_dir, "claude.json"), "w") as f:
            json.dump(claude_legacy, f)

        limits.import_legacy_snapshots()
        cl = limits.load_provider_limits("claude")
        self.assertEqual(cl["five_hour"]["used_pct"], 8)
        self.assertEqual(cl["seven_day"]["used_pct"], 3)

    def test_levels_and_summary_line(self):
        from foreman import limits

        # Initially unknown
        self.assertEqual(limits.levels(), {"claude": "unknown", "antigravity": "unknown"})
        self.assertEqual(limits.summary_line(), "Claude ? | agy ?")

        os.environ["FOREMAN_NOW"] = "2026-11-05T13:10:00Z"
        limits.record_reading("claude", {
            "five_hour": {"used_pct": 42, "resets_at": "2026-11-05T17:00:00Z"},
            "seven_day": {"used_pct": 61, "resets_at": "2026-11-12T12:00:00Z"},
        }, observed_at="2026-11-05T13:06:00Z")

        limits.record_reading("antigravity", {
            "five_hour": {"used_pct": 10, "resets_at": "2026-11-05T17:00:00Z"},
            "weekly": {"used_pct": 33, "resets_at": "2026-11-12T12:00:00Z"},
        }, observed_at="2026-11-05T11:10:00Z")

        self.assertEqual(limits.levels(), {"claude": "ok", "antigravity": "ok"})
        # 4m ago for claude, 2h ago for agy
        self.assertEqual(limits.summary_line(), "Claude wk 61% · 5h 42% (4m) | agy wk 33% · 5h 10% (2h)")

        # Warn threshold crossing (>= 70)
        limits.record_reading("claude", {
            "seven_day": {"used_pct": 75, "resets_at": "2026-11-12T12:00:00Z"},
        }, observed_at="2026-11-05T13:09:00Z")
        self.assertEqual(limits.levels()["claude"], "warn")

        # Stop threshold crossing (>= 90)
        limits.record_reading("claude", {
            "seven_day": {"used_pct": 92, "resets_at": "2026-11-12T12:00:00Z"},
        }, observed_at="2026-11-05T13:09:00Z")
        self.assertEqual(limits.levels()["claude"], "stop")

    def test_context_stored_on_session(self):
        from foreman import limits, sessions

        self.init_project("demo")
        s_claude = sessions.create_session("demo", "claude", agent_session_id="claude-sess-1", title="c1")
        s_agy = sessions.create_session("demo", "antigravity", transport={"kind": "fake", "handle": "term_xyz"}, title="a1")

        # Claude context via current_usage
        payload_claude = {
            "session_id": "claude-sess-1",
            "context_window": {
                "current_usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_creation_input_tokens": 500,
                    "cache_read_input_tokens": 300,
                }
            }
        }
        limits.store_session_context("claude", payload_claude)
        loaded = sessions.load_session(s_claude["sid"])
        self.assertEqual(loaded["context_tokens"], 2000)

        # Antigravity context via total_input_tokens and ORCA_TERMINAL_HANDLE
        os.environ["ORCA_TERMINAL_HANDLE"] = "term_xyz"
        payload_agy = {
            "context_window": {
                "total_input_tokens": 45000,
            }
        }
        limits.store_session_context("antigravity", payload_agy)
        loaded_agy = sessions.load_session(s_agy["sid"])
        self.assertEqual(loaded_agy["context_tokens"], 45000)

    def test_cli_tap_and_limits(self):
        # 1. Tap statusline
        payload = json.dumps({
            "rate_limits": {
                "five_hour": {"used_percentage": 22, "resets_at": 1793530800},
                "seven_day": {"used_percentage": 33, "resets_at": 1793998800},
            }
        })
        code, out, err = run_cli("tap", "statusline", "--agent", "claude", "--", "echo", "hello-tap", stdin=payload)
        self.assertEqual(code, 0, err)
        self.assertIn("hello-tap", out)

        # 2. Check limits --line
        code, out, err = run_cli("limits", "--line")
        self.assertEqual(code, 0)
        self.assertIn("Claude wk 33% · 5h 22%", out)

        # 3. Check limits --json
        code, out, err = run_cli("limits", "--json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("claude", data)
        self.assertEqual(data["claude"]["windows"]["five_hour"]["used_pct"], 22)

        # 4. Check limits default human-readable
        code, out, err = run_cli("limits")
        self.assertEqual(code, 0)
        self.assertIn("Claude", out)
        self.assertIn("five_hour", out)

        # 5. Tap error is swallowed
        code, out, err = run_cli("tap", "statusline", "--agent", "claude", "--", "echo", "still-runs", stdin="not json")
        self.assertEqual(code, 0)
        self.assertIn("still-runs", out)

