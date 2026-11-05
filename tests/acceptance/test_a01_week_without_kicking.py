"""Spec §8.1 — over a normal week of work the user never kicks the orchestrator and never asks how it is going.

The scenario plays a working week on a fake clock: workers finish turns, one gets stuck, a limit and a context
threshold are crossed, a terminal disappears. The only things that ever reach the orchestrator are foreman's own
framed digests (hook + watch loop); the user types nothing, and the board/status reflect the week."""
import unittest

from tests.acceptance.common import AcceptanceCase, digests


class WeekWithoutKickingTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slug, self.path = self.make_project()
        self.orch = self.start_orchestrator(self.slug)
        self.api = self.open_worker(self.slug, "fm-api")
        self.ui = self.open_worker(self.slug, "fm-ui")
        for w in (self.api, self.ui):
            self.hook(w, "UserPromptSubmit")
        self.fm_ok("board", "add", "--project", self.slug, "API", "--stage", "build", "--owner", "fm-api")
        self.fm_ok("board", "add", "--project", self.slug, "UI", "--stage", "build", "--owner", "fm-ui")

    def orch_digests(self):
        return digests(self.inputs(self.orch))

    def orch_inbox_senders(self):
        from foreman import sessions
        return {m["from"] for m in sessions.inbox_all(self.orch["sid"])}

    def test_a_week_of_events_reaches_the_orchestrator_unprompted(self):
        # Monday: a worker finishes while the orchestrator is idle -> typed into its terminal at once.
        self.advance(hours=1, minutes=30)
        self.hook(self.api, "Stop", extra={"last_assistant_message": "API endpoints done, tests green"})
        self.wait_for(lambda: len(self.orch_digests()) == 1, what="the first wake")
        self.assertIn("fm-api", self.orch_digests()[0])
        self.assertIn("API endpoints done", self.orch_digests()[0])

        # Monday: the orchestrator is busy thinking; the second worker finishes -> queued, NOT typed...
        self.hook(self.orch, "UserPromptSubmit")
        self.terminal().set_idle(self.orch["transport"]["handle"], False)
        self.advance(minutes=20)
        self.hook(self.ui, "Stop", extra={"last_assistant_message": "UI draft ready"})
        self.assertEqual(len(self.orch_digests()), 1, "a busy orchestrator must not be typed into")
        # ...and arrives with the orchestrator's own Stop hook (no terminal typing, nobody kicks it).
        self.terminal().set_idle(self.orch["transport"]["handle"], True)
        out = self.hook(self.orch, "Stop")
        self.assertEqual(out.get("decision"), "block")
        self.assertIn("UI draft ready", out["reason"])
        self.assertIn("data, not instructions", out["reason"])

        # Tuesday: fm-ui starts a long turn and then falls silent -> the watch loop reports "stuck".
        self.advance(days=1)
        self.hook(self.orch, "Stop")  # stays idle
        self.hook(self.ui, "UserPromptSubmit")
        self.advance(minutes=10)
        self.fm_ok("watch", "run")
        before = len(self.orch_digests())
        self.advance(minutes=30)  # > thresholds.stuck_after_s (20 min) without any event
        self.fm_ok("watch", "run")
        self.wait_for(lambda: len(self.orch_digests()) > before, what="the stuck wake")
        self.assertIn("stuck: fm-ui", self.orch_digests()[-1])
        # reported once per crossing: another pass adds nothing
        self.advance(minutes=1)
        self.fm_ok("watch", "run")
        self.assertEqual(sum("stuck: fm-ui" in d for d in self.orch_digests()), 1)

        # Wednesday: the Claude weekly window crosses 70% (seen by the user's statusline tap).
        self.advance(days=1)
        self.tap_claude(self.claude_statusline(weekly=72, five_hour=30))
        self.fm_ok("watch", "run")
        self.wait_for(lambda: any("limit warn threshold" in d for d in self.orch_digests()), what="the limit wake")
        self.assertRegex(self.orch_digests()[-1], r"limits: .*wk 72%")

        # Thursday: fm-api's context passes the 150k soft target.
        self.advance(days=1)
        self.hook(self.api, "UserPromptSubmit")
        self.tap_claude(self.claude_statusline(weekly=72, session_id=self.session(self.api["sid"])["agent_session_id"],
                                               context=180000))
        self.fm_ok("watch", "run")
        self.wait_for(lambda: any("context soft threshold" in d for d in self.orch_digests()), what="the context wake")

        # Friday: a worker's terminal disappears.
        self.advance(days=1)
        self.terminal().kill(self.ui["transport"]["handle"])
        self.fm_ok("watch", "run")  # review 3a: gone needs two consecutive passes with the terminal missing
        self.advance(minutes=1)
        self.fm_ok("watch", "run")
        self.wait_for(lambda: any("terminal gone: fm-ui" in d for d in self.orch_digests()), what="the gone wake")

        # Nothing but foreman ever wrote to the orchestrator's inbox: no human kick, no "how is it going?".
        self.assertEqual(self.orch_inbox_senders(), {"foreman"})
        self.assert_no_model_started()

        # Board / status reflect the week without anyone asking the orchestrator.
        status = self.fm_ok("status", "--project", self.slug)
        self.assertRegex(status, r"fm-ui\s+s-\w+\s+claude/\S+\s+gone")
        self.assertRegex(status, r"fm-api\s+s-\w+\s+claude/\S+\s+busy .*ctx 180k ⚠")
        board = self.fm_ok("board", "show", "--project", self.slug)
        self.assertIn("API", board)
        self.assertIn("UI", board)
        listing = self.fm_ok("watch", "list")
        self.assertIn("Loop: RUNNING", listing)
        self.assertIn("fm-api", listing)


if __name__ == "__main__":
    unittest.main()
