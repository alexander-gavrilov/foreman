"""Spec §8.4 — with nothing happening for a day, foreman spends zero model tokens.

Two ways of looking at one idle day on a fake clock: the watch loop run through the CLI every 30 minutes, and the
same loop run in-process every five minutes (288 passes). In both: no byte typed into any agent terminal, no
inbox message, no Telegram message, no model CLI started. The heartbeat proves the loop really ran."""
import os
import unittest

from tests.acceptance.common import AcceptanceCase, digests, iso


class IdleDayTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.start_telegram()
        self.slug, self.path = self.make_project()
        self.orch = self.start_orchestrator(self.slug)
        self.worker = self.open_worker(self.slug, "fm-api")
        self.hook(self.worker, "UserPromptSubmit")
        self.hook(self.worker, "Stop", extra={"last_assistant_message": "done"})
        self.wait_for(lambda: digests(self.inputs(self.orch)), what="the finish wake before the quiet day")
        self.tap_claude(self.claude_statusline(weekly=41, five_hour=12))  # an ok reading, nothing to report
        self.baseline = self.snapshot()

    def snapshot(self):
        from foreman import sessions
        return {
            "orch_inputs": self.inputs(self.orch),
            "worker_inputs": self.inputs(self.worker),
            "orch_inbox": len(sessions.inbox_all(self.orch["sid"])),
            "worker_inbox": len(sessions.inbox_all(self.worker["sid"])),
            "telegram": len(self.telegram.sent_messages),
        }

    def assert_nothing_spent(self):
        self.assertEqual(self.snapshot(), self.baseline, "something was sent during the idle day")
        self.assert_no_model_started()

    def test_a_day_of_watch_passes_through_the_cli_sends_nothing(self):
        for _ in range(48):  # every 30 minutes for 24 hours
            self.advance(minutes=30)
            self.fm_ok("watch", "run")
        self.assertEqual(iso(self.clock), "2026-11-03T09:00:00Z")
        self.assert_nothing_spent()
        self.assertIn("Loop: RUNNING", self.fm_ok("watch", "list"))

    def test_a_day_of_five_minute_ticks_sends_nothing(self):
        from foreman import paths, store, watch
        os.environ["FOREMAN_NOW"] = iso(self.clock)
        wakes = 0
        for _ in range(24 * 12):
            self.advance(minutes=5)
            os.environ["FOREMAN_NOW"] = iso(self.clock)
            summary = watch.run_pass()
            wakes += summary["wakes"] + summary["deliveries"]
        self.assertEqual(wakes, 0)
        self.assert_nothing_spent()
        beat = store.read_json(paths.watch_heartbeat_file())
        self.assertEqual(beat["last_run_at"], iso(self.clock))


if __name__ == "__main__":
    unittest.main()
