"""Review 3a fixes: F5 (single orchestrator), F6, F7, F8 (guard)."""
from tests.test_cmd_sessions import SessionTestCase, run_main
from tests.helpers import ForemanTestCase


class GuardRulesTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import guard
        self.g = guard

    def test_open_role_and_owner_are_denied_for_an_orchestrator(self):
        for cmd in ("foreman open --project demo --purpose x --role orchestrator",
                    "foreman open --purpose x --role=orchestrator", "foreman open --purpose x --ro orchestrator",
                    "foreman open --purpose x --owner user", "foreman open --purpose x --ow=user",
                    "foreman open --purpose x --owner orchestrator"):
            self.assertIsNotNone(self.g.check_command(cmd), cmd)
        for cmd in ("foreman open --project demo --agent claude --purpose x --prompt hi",
                    "foreman open --purpose x --kind review"):
            self.assertIsNone(self.g.check_command(cmd), cmd)

    def test_close_user_consent_is_denied_for_an_orchestrator(self):
        for cmd in ("foreman close fm-api --user-consent", "foreman close fm-api --user-c",
                    "foreman close fm-api --use", "foreman close --u fm-api", "foreman close fm-api --user-consent=1"):
            self.assertIsNotNone(self.g.check_command(cmd), cmd)
        self.assertIsNone(self.g.check_command("foreman close fm-api"))
        self.assertIsNone(self.g.check_command("foreman close fm-api --json"))

    def test_tree_cannot_write_html_into_directories(self):
        for cmd in ("tree -R -H . -L 1", "tree -R", "tree -LR 1", "tree -aR", "tree -H .", "tree -RH .",
                    "tree -dH base", "tree -L 1 -R"):
            self.assertIsNotNone(self.g.check_command(cmd), cmd)
        for cmd in ("tree -L 2", "tree -a -d", "tree -I node_modules"):
            self.assertIsNone(self.g.check_command(cmd), cmd)

    def test_agent_isolation_is_denied_for_an_orchestrator(self):
        base = {"tool_name": None, "tool_input": None, "command": None}
        for name in ("Agent", "Task", "agent"):
            for iso in ("remote", "worktree"):
                n = dict(base, tool_name=name, tool_input={"prompt": "x", "isolation": iso})
                self.assertIsNotNone(self.g.check_tool(n), (name, iso))
            self.assertIsNone(self.g.check_tool(dict(base, tool_name=name, tool_input={"prompt": "x"})), name)
            self.assertIsNone(self.g.check_tool(dict(base, tool_name=name, tool_input=None)), name)
            self.assertIsNone(self.g.check_tool(dict(base, tool_name=name, tool_input={"isolation": None})), name)


class SingleOrchestratorTest(SessionTestCase):
    def test_open_role_orchestrator_is_refused_while_one_is_live(self):
        from foreman import projects
        code, out, err = run_main("orchestrator", "start", "--project", self.slug, "--transport", "fake")
        self.assertEqual(code, 0, err)
        first = projects.load_project(self.slug)["orchestrator"]
        code, out, err = run_main("open", "--project", self.slug, "--purpose", "x", "--role", "orchestrator",
                                  "--transport", "fake", "--prompt", "p")
        self.assertNotEqual(code, 0)
        self.assertIn("orchestrator", err)
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], first)
        self.assertEqual(len([s for s in self.sessions() if s["role"] == "orchestrator"]), 1)

    def test_open_role_orchestrator_works_when_none_is_live(self):
        code, out, err = run_main("open", "--project", self.slug, "--purpose", "x", "--role", "orchestrator",
                                  "--transport", "fake", "--prompt", "p")
        self.assertEqual(code, 0, err)

    def test_rotating_the_orchestrator_is_still_possible(self):
        code, out, err = run_main("orchestrator", "start", "--project", self.slug, "--transport", "fake")
        self.assertEqual(code, 0, err)
        orch = [s for s in self.sessions() if s["role"] == "orchestrator"][0]
        self.fake.set_idle(orch["transport"]["handle"], True)
        from foreman import sessions
        sessions.set_state(orch["sid"], "idle")
        code, out, err = run_main("rotate", orch["sid"])
        self.assertEqual(code, 0, err)
