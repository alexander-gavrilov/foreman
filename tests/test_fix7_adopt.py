"""Fix round 7 (review 6 F4): `orchestrator adopt` is the recovery for an agent restarted by hand in its terminal."""
import contextlib
import io

from tests.test_fix6_child import start
from tests.test_hooks import HookCase, claude


def run_main(*args):
    from foreman import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class AdoptRecoveryTest(HookCase):
    def crashed_orchestrator(self):
        orch, env = self.pending_launch(role="orchestrator", title="orch", state="busy",
                                        agent_session_id="orch-1", transcript_path="/tmp/orch-1.jsonl")
        cwd = self.proj["path"]
        self.sessions.inbox_enqueue(orch["sid"], "from a worker", sender="w")
        # the agent crashed (no SessionEnd); the user started plain `claude` again in the same terminal
        self.hook("claude", "SessionStart", start("orch-2", cwd, source="startup"), env)
        self.assertEqual(self.rec(orch["sid"])["agent_session_id"], "orch-1")  # taken for a child
        return orch, env, cwd

    def check_recovered(self, orch, env, cwd):
        sid = orch["sid"]
        self.assertIsNone(self.rec(sid).get("agent_session_id"))
        out = self.hook("claude", "SessionStart", start("orch-2", cwd, source="startup"), env)
        self.assertIn("hookSpecificOutput", out)
        self.assertEqual(self.rec(sid)["agent_session_id"], "orch-2")
        stop = self.hook("claude", "Stop", claude("Stop", sid="orch-2", cwd=cwd), env)
        self.assertIn("from a worker", str(stop))  # the inbox is delivered
        self.assertEqual(self.rec(sid)["state"], "busy")
        self.assertEqual(self.sessions.inbox_pending(sid), [])
        self.assertEqual(self.rec(sid)["role"], "orchestrator")

    def test_adopt_by_sid_then_the_next_hook_rebinds(self):
        orch, env, cwd = self.crashed_orchestrator()
        code, out, err = run_main("orchestrator", "adopt", orch["sid"], "--project", self.slug)
        self.assertEqual(code, 0, err)
        self.assertIn("next hook", out)
        self.check_recovered(orch, env, cwd)

    def test_adopt_by_terminal_handle_works_too(self):
        orch, env, cwd = self.crashed_orchestrator()
        code, out, err = run_main("orchestrator", "adopt", env["ORCA_TERMINAL_HANDLE"], "--project", self.slug,
                                  "--transport", "fake")
        self.assertEqual(code, 0, err)
        self.check_recovered(orch, env, cwd)

    def test_adopting_a_healthy_session_keeps_it_working(self):
        w, env = self.pending_launch(title="mine", state="idle", agent_session_id="mine-1")
        code, out, err = run_main("orchestrator", "adopt", w["sid"], "--project", self.slug)
        self.assertEqual(code, 0, err)
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="mine-1", cwd=self.proj["path"]), env)
        got = self.rec(w["sid"])
        self.assertEqual((got["agent_session_id"], got["state"], got["role"]), ("mine-1", "busy", "orchestrator"))
        self.assertEqual([e for e in self.events() if e.get("kind") == "child_session"], [])

    def test_json_output_is_still_plain_json(self):
        import json
        w, env = self.pending_launch(title="mine", state="idle", agent_session_id="mine-1")
        code, out, err = run_main("orchestrator", "adopt", w["sid"], "--project", self.slug, "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["sid"], w["sid"])
