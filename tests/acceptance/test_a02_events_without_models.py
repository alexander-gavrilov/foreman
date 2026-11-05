"""Spec §8.2 — when a worker finishes, gets stuck or crosses a threshold, the orchestrator learns about it without
being asked and WITHOUT any model call: the wake travels hook -> inbox -> transport (the fake terminal records
what was typed), and no `claude`/`agy`/`codex` process is ever started."""
import unittest

from tests.acceptance.common import AcceptanceCase, digests


class EventsWithoutModelsTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slug, self.path = self.make_project()
        self.orch = self.start_orchestrator(self.slug)
        self.worker = self.open_worker(self.slug, "fm-api")
        self.hook(self.worker, "UserPromptSubmit")

    def typed(self):
        return digests(self.inputs(self.orch))

    def delivered_rows(self):
        from foreman import sessions
        return [m for m in sessions.inbox_all(self.orch["sid"]) if m.get("delivered_at")]

    def test_worker_finish_is_pushed_to_the_orchestrator_terminal(self):
        self.hook(self.worker, "Stop", extra={"last_assistant_message": "refactor finished"})
        self.wait_for(lambda: self.typed(), what="the finish wake")
        self.assertIn("fm-api", self.typed()[0])
        self.assertIn("refactor finished", self.typed()[0])
        self.wait_for(self.delivered_rows, what="the inbox row to be marked delivered")
        self.assert_no_model_started()

    def test_stuck_worker_is_pushed_by_the_watch_loop(self):
        self.advance(minutes=45)  # busy, no event for longer than thresholds.stuck_after_s
        out = self.fm_ok("watch", "run")
        self.assertIn("1 wakes", out)
        self.assertIn("stuck: fm-api", self.typed()[0])
        self.assert_no_model_started()

    def test_threshold_crossings_wake_the_orchestrator_and_wind_workers_down(self):
        self.tap_claude(self.claude_statusline(weekly=75))
        self.fm_ok("watch", "run")
        self.assertIn("limit warn threshold reached (75%)", "\n".join(self.typed()))
        self.tap_claude(self.claude_statusline(weekly=93))
        self.advance(minutes=1)
        self.fm_ok("watch", "run")
        self.assertIn("limit stop threshold reached (93%)", "\n".join(self.typed()))
        # the running Claude worker also got a queued "wind down and hand off" for its next idle point
        from foreman import sessions
        winddown = [m for m in sessions.inbox_all(self.worker["sid"]) if "wind down" in m["text"]]
        self.assertEqual(len(winddown), 1)
        self.assertEqual(winddown[0]["from"], "foreman")
        # the same crossing is not reported twice
        self.advance(minutes=1)
        self.fm_ok("watch", "run")
        self.assertEqual(sum("limit stop threshold" in t for t in self.typed()), 1)
        self.assert_no_model_started()

    def test_a_busy_orchestrator_gets_the_event_at_its_next_idle_without_typing(self):
        self.hook(self.orch, "UserPromptSubmit")
        self.terminal().set_idle(self.orch["transport"]["handle"], False)
        self.hook(self.worker, "Stop", extra={"last_assistant_message": "done"})
        self.assertEqual(self.typed(), [])
        self.terminal().set_idle(self.orch["transport"]["handle"], True)
        out = self.hook(self.orch, "Stop")
        self.assertEqual(out["decision"], "block")
        self.assertIn("fm-api", out["reason"])
        self.assert_no_model_started()


if __name__ == "__main__":
    unittest.main()
