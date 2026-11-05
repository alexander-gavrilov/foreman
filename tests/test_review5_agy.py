"""Review 5 R5-1: real agy payloads carry lastUserInput on every hook (the turn's user prompt) and
invocationNum on PreInvocation, so a Stop continuation's own PreInvocation must confirm, not release."""
import os
from unittest.mock import patch

from tests.test_hooks import HookCase

USER_PROMPT = "Run the shell command: echo hi . Then answer with the single word done."


class AgyRealPayloadContinuationTest(HookCase):
    def setUp(self):
        super().setUp()
        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:00Z"
        self.rec, self.env = self.pending_launch(agent="antigravity", title="ag")
        self.sessions.inbox_enqueue(self.rec["sid"], "please wrap up", sender="orch")
        self.handle = self.env["ORCA_TERMINAL_HANDLE"]

    def common(self, **extra):
        p = {"conversationId": "a", "workspacePaths": [self.proj["path"]], "modelName": "gemini-3.8-flash-low",
             "lastUserInput": USER_PROMPT, "transcriptPath": "/x/transcript_full.jsonl"}
        p.update(extra)
        return p

    def stop(self):
        return self.hook("antigravity", "Stop", self.common(fullyIdle=True, executionNum=0, error="",
                                                            terminationReason="NO_TOOL_CALL"), self.env)

    def pre_invocation(self, n, prompt=USER_PROMPT):
        return self.hook("antigravity", "PreInvocation",
                         self.common(lastUserInput=prompt, invocationNum=n, initialNumSteps=9), self.env)

    def pre_tool(self):
        return self.hook("antigravity", "PreToolUse",
                         self.common(stepIdx=11, toolCall={"name": "run_command", "args": {"CommandLine": "ls"}}),
                         self.env)

    def row(self):
        return self.sessions.inbox_all(self.rec["sid"])[0]

    def watch_after(self, seconds):
        from datetime import datetime, timedelta, timezone
        from foreman import watch
        t = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        os.environ["FOREMAN_NOW"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        with patch("foreman.wake.wake"), patch("foreman.notify.notify"):
            watch.run_pass()

    def test_continuation_taken_yields_exactly_one_delivery(self):
        self.assertEqual(self.stop()["decision"], "continue")
        self.pre_invocation(2)  # the continuation: same lastUserInput as the turn's prompt
        self.pre_tool()
        self.assertEqual(self.row()["claimed_by"], "stop-hook")
        self.assertNotEqual(self.stop().get("decision"), "continue")  # not delivered a second time
        self.watch_after(120)
        self.assertEqual(self.ft.inputs(self.handle), [])  # and never typed
        self.assertEqual(len(self.sessions.inbox_all(self.rec["sid"])), 1)

    def test_a_continuation_with_invocation_one_and_unchanged_prompt_still_confirms(self):
        self.stop()
        self.pre_invocation(1)
        self.assertEqual(self.row()["claimed_by"], "stop-hook")

    def test_continuation_not_taken_is_retyped_once(self):
        self.assertEqual(self.stop()["decision"], "continue")  # cap hit: agy ignores it, no more events
        self.watch_after(120)
        typed = self.ft.inputs(self.handle)
        self.assertEqual(len(typed), 1)
        self.assertIn("please wrap up", typed[0])
        self.watch_after(300)
        self.assertEqual(len(self.ft.inputs(self.handle)), 1)

    def test_a_new_real_user_prompt_releases_and_is_delivered(self):
        self.stop()
        self.pre_invocation(1, prompt="Something else entirely, please")
        row = self.row()
        self.assertIsNone(row["delivered_at"])
        self.assertNotIn("claimed_by", row)
        self.assertEqual(self.stop()["decision"], "continue")  # arrives with the next digest, once
        self.pre_invocation(2, prompt="Something else entirely, please")
        self.assertEqual(self.row()["claimed_by"], "stop-hook")
        self.assertNotEqual(self.stop().get("decision"), "continue")
