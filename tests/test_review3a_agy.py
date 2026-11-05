"""Review 3a F11 (agy continuation claims are provisional) and F12 (Orca wait_idle uses worktree ps)."""
import os
from unittest.mock import patch

from tests.test_hooks import HookCase
from tests.helpers import ForemanTestCase
from tests.stubutil import StubMixin
from tests.test_transports_cli import ok


def agy_stop(proj, full=True):
    return {"conversationId": "a", "workspacePaths": [proj["path"]], "fullyIdle": full}


class AgyProvisionalClaimTest(HookCase):
    def setUp(self):
        super().setUp()
        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:00Z"
        self.rec, self.env = self.pending_launch(agent="antigravity", title="ag")
        self.sessions.inbox_enqueue(self.rec["sid"], "please wrap up", sender="orch")

    def stop(self):
        return self.hook("antigravity", "Stop", agy_stop(self.proj), self.env)

    def test_agy_claim_is_provisional_and_claude_claim_is_not(self):
        self.assertEqual(self.stop()["decision"], "continue")
        row = self.sessions.inbox_all(self.rec["sid"])[0]
        self.assertIsNotNone(row["delivered_at"])
        self.assertEqual(row["claimed_by"], "stop-hook:provisional")
        c, cenv = self.pending_launch(title="cl")
        self.sessions.inbox_enqueue(c["sid"], "x")
        from tests.test_hooks import claude
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], sid="c1"), cenv)
        self.assertEqual(self.sessions.inbox_all(c["sid"])[0]["claimed_by"], "stop-hook")

    def test_next_agent_activity_confirms_the_continuation(self):
        self.stop()
        self.hook("antigravity", "PreInvocation", agy_stop(self.proj), self.env)
        row = self.sessions.inbox_all(self.rec["sid"])[0]
        self.assertEqual(row["claimed_by"], "stop-hook")

    def _watch_after(self, seconds):
        from datetime import datetime, timedelta, timezone
        from foreman import watch
        t = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)
        os.environ["FOREMAN_NOW"] = t.strftime("%Y-%m-%dT%H:%M:%SZ")
        with patch("foreman.wake.wake"), patch("foreman.notify.notify"):
            watch.run_pass()

    def test_continuation_not_taken_is_typed_at_idle_by_the_watch(self):
        self.stop()  # the cap was hit: agy ignores "continue", no further event arrives
        self._watch_after(10)  # inside the grace period: untouched
        self.assertEqual(self.ft.inputs(self.env["ORCA_TERMINAL_HANDLE"]), [])
        self._watch_after(120)
        typed = self.ft.inputs(self.env["ORCA_TERMINAL_HANDLE"])
        self.assertEqual(len(typed), 1)
        self.assertIn("please wrap up", typed[0])
        self.assertEqual(self.rec_state(), "idle")
        row = self.sessions.inbox_all(self.rec["sid"])[0]
        self.assertEqual(row["claimed_by"], "deliver")

    def test_confirmed_continuation_is_not_typed_again(self):
        self.stop()
        self.hook("antigravity", "PreInvocation", agy_stop(self.proj), self.env)
        self._watch_after(120)
        self.assertEqual(self.ft.inputs(self.env["ORCA_TERMINAL_HANDLE"]), [])

    def rec_state(self):
        return self.sessions.load_session(self.rec["sid"])["state"]


class OrcaWaitIdleTest(StubMixin, ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.setup_stubs()
        from foreman import transports
        from foreman.transports import base
        self.base = base
        self.t = transports.get_transport("orca")

    LIST = {"match": ["terminal", "list"], "json": ok({"terminals": [
        {"handle": "term_a", "title": "a", "worktreePath": "/w", "agentIdentity": "antigravity",
         "connected": True, "tabId": "T1", "leafId": "L1"}]})}

    def ps(self, state):
        return {"match": ["worktree", "ps"], "json": ok({"worktrees": [{"agents": [
            {"paneKey": "T1:L1", "state": state}]}]})}

    def test_wait_idle_true_from_worktree_ps_without_tui_idle(self):
        self.set_rules([self.LIST, self.ps("done")])
        self.assertTrue(self.t.wait_idle("term_a", 5))
        self.assertNotIn("wait", [c[1] for c in self.calls("orca") if len(c) > 1 and c[0] == "terminal"])

    def test_wait_idle_false_when_ps_says_working_until_timeout(self):
        self.set_rules([self.LIST, self.ps("working")])
        with patch("time.sleep"):
            self.assertFalse(self.t.wait_idle("term_a", 0.05))

    def test_wait_idle_turns_true_when_the_agent_finishes(self):
        self.set_rules([self.LIST, self.ps("working")])
        states = iter([False, False, True])
        with patch.object(type(self.t), "_idle_now", side_effect=lambda h: next(states)), patch("time.sleep"):
            self.assertTrue(self.t.wait_idle("term_a", 30))

    def test_wait_idle_gone_terminal(self):
        self.set_rules([{"match": ["terminal", "list"], "json": ok({"terminals": []})}])
        with self.assertRaises(self.base.TerminalGone):
            self.t.wait_idle("term_a", 1)
