"""Fix round 7 (review 6 F3, F8): successor sources and the sdk entrypoint on a record without a session id."""
from tests.test_fix6_child import start
from tests.test_hooks import HookCase, claude


class SuccessorTest(HookCase):
    def setUp(self):
        super().setUp()
        self.orch, self.env = self.pending_launch(role="orchestrator", title="orch", state="idle",
                                                  agent_session_id="orch-1", transcript_path="/tmp/orch-1.jsonl")
        self.cwd = self.proj["path"]

    def test_fork_is_a_successor(self):  # F3
        out = self.hook("claude", "SessionStart", start("orch-fork", self.cwd, source="fork"), self.env)
        self.assertIn("hookSpecificOutput", out)  # briefed
        got = self.rec(self.orch["sid"])
        self.assertEqual((got["agent_session_id"], got["past_agent_session_ids"]), ("orch-fork", ["orch-1"]))
        stop = self.hook("claude", "Stop", claude("Stop", sid="orch-fork", cwd=self.cwd), self.env)
        self.assertEqual(self.rec(self.orch["sid"])["state"], "idle")
        self.assertEqual([e for e in self.events() if e.get("kind") == "child_session"], [])
        self.assertIsInstance(stop, dict)

    def test_sdk_child_does_not_claim_a_record_without_a_session_id(self):  # F8
        rec, env = self.pending_launch(title="launched", role="orchestrator", state="starting")
        env = dict(env, CLAUDE_CODE_ENTRYPOINT="sdk-cli")
        out = self.hook("claude", "SessionStart", start("kid-1", self.cwd, source="startup"), env)
        self.assertNotIn("hookSpecificOutput", out)
        self.assertIsNone(self.rec(rec["sid"]).get("agent_session_id"))
        self.hook("claude", "SessionEnd", claude("SessionEnd", sid="kid-1", cwd=self.cwd, reason="other"), env)
        self.assertEqual(self.rec(rec["sid"])["lifecycle"], "active")
        # the real session still binds afterwards
        env2 = {k: v for k, v in env.items() if k != "CLAUDE_CODE_ENTRYPOINT"}
        out = self.hook("claude", "SessionStart", start("real-1", self.cwd, source="startup"), env2)
        self.assertIn("hookSpecificOutput", out)
        self.assertEqual(self.rec(rec["sid"])["agent_session_id"], "real-1")

    def test_sdk_child_does_not_become_the_pending_orchestrator(self):  # F8, D54 stray
        self.sessions.mark_pending_orchestrator(self.slug, "claude", self.cwd)
        env = {"ORCA_TERMINAL_HANDLE": "stray-term", "CLAUDE_CODE_ENTRYPOINT": "sdk-cli"}
        self.hook("claude", "SessionStart", start("kid-2", self.cwd, source="startup"), env)
        made = [r for r in self.sessions.list_sessions() if r.get("agent_session_id") == "kid-2"]
        self.assertTrue(all(r.get("role") != "orchestrator" and not r.get("pending_orchestrator") for r in made))
