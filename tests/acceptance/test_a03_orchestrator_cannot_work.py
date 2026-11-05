"""Spec §8.3 — an attempt by the orchestrator to edit project files is blocked.

Real PreToolUse payloads (Claude Code and Antigravity dialects) are piped into `foreman hook`; the hook's stdout is
what the agent sees. Edits, writes, shell writes, MCP tools and git mutations are denied with a message telling the
orchestrator to delegate; read-only tools and `foreman` itself pass; workers are not restricted."""
import os
import unittest

from tests.acceptance.common import AcceptanceCase


def denied_claude(out):
    spec = out.get("hookSpecificOutput") or {}
    return spec.get("hookEventName") == "PreToolUse" and spec.get("permissionDecision") == "deny"


def denied_agy(out):
    return out.get("decision") == "deny"


class OrchestratorCannotWorkTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slug, self.path = self.make_project()
        self.target = os.path.join(self.path, "app.py")
        with open(self.target, "w") as f:
            f.write("print('hello')\n")

    def pre_tool_claude(self, rec, tool, tool_input, **extra):
        return self.hook(rec, "PreToolUse", extra=dict({"tool_name": tool, "tool_input": tool_input}, **extra))

    def pre_tool_agy(self, rec, tool, args):
        return self.hook(rec, "PreToolUse", extra={"toolCall": {"name": tool, "args": args}})

    def test_claude_orchestrator_edits_are_denied_with_a_delegate_message(self):
        orch = self.start_orchestrator(self.slug)
        for tool, tool_input in (("Edit", {"file_path": self.target, "old_string": "hello", "new_string": "bye"}),
                                 ("Write", {"file_path": self.target, "content": "x"}),
                                 ("MultiEdit", {"file_path": self.target, "edits": []}),
                                 ("NotebookEdit", {"notebook_path": "n.ipynb", "new_source": "x"}),
                                 ("mcp__github__create_issue", {"title": "t"})):
            out = self.pre_tool_claude(orch, tool, tool_input)
            self.assertTrue(denied_claude(out), "%s was not denied: %s" % (tool, out))
            self.assertIn("foreman open", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_claude_orchestrator_shell_writes_are_denied_and_reads_pass(self):
        orch = self.start_orchestrator(self.slug)
        for cmd in ("echo x > app.py", "sed -i s/hello/bye/ app.py", "rm -rf build", "git commit -am wip",
                    "git push origin main", "cat > app.py <<EOF\nx\nEOF", "python3 -c 'open(\"app.py\",\"w\")'",
                    "foo=$(rm app.py)", "ls | tee app.py", "touch new.py", "mv app.py b.py",
                    "foreman board done t1 --check 'make'"):
            out = self.pre_tool_claude(orch, "Bash", {"command": cmd})
            self.assertTrue(denied_claude(out), "Bash %r was not denied: %s" % (cmd, out))
        for tool, tool_input in (("Bash", {"command": "git status"}), ("Bash", {"command": "foreman status | head -5"}),
                                 ("Bash", {"command": "ls -la && git log --oneline"}),
                                 ("Read", {"file_path": self.target}), ("Grep", {"pattern": "hello"}),
                                 ("Glob", {"pattern": "*.py"}), ("SendMessage", {"to": "x", "message": "y"})):
            self.assertEqual(self.pre_tool_claude(orch, tool, tool_input), {}, "%s %s must pass" % (tool, tool_input))
        with open(self.target) as f:
            self.assertEqual(f.read(), "print('hello')\n")

    def test_subagents_of_the_orchestrator_are_guarded_too(self):
        orch = self.start_orchestrator(self.slug)
        out = self.pre_tool_claude(orch, "Edit", {"file_path": self.target}, agent_id="sub-1", agent_type="general")
        self.assertTrue(denied_claude(out))

    def test_the_block_is_logged_as_evidence(self):
        from foreman import paths, store
        orch = self.start_orchestrator(self.slug)
        self.pre_tool_claude(orch, "Write", {"file_path": self.target, "content": "x"})
        blocked = [e for e in store.read_jsonl(paths.events_file()) if e.get("blocked")]
        self.assertEqual([(e["sid"], e["tool"]) for e in blocked], [(orch["sid"], "Write")])

    def test_garbled_orchestrator_input_fails_closed(self):
        orch = self.start_orchestrator(self.slug)
        for bad_input in ({"command": ["rm", "-rf", "x"]}, {"command": None}, {}, "rm -rf x"):
            out = self.pre_tool_claude(orch, "Bash", bad_input)
            self.assertTrue(denied_claude(out), "garbled Bash input %r was allowed: %s" % (bad_input, out))

    def test_antigravity_orchestrator_is_guarded_in_its_own_dialect(self):
        orch = self.start_orchestrator(self.slug, agent="antigravity", title="agy-orch")
        self.assertTrue(denied_agy(self.pre_tool_agy(orch, "write_to_file", {"TargetFile": self.target})))
        self.assertTrue(denied_agy(self.pre_tool_agy(orch, "replace_file_content", {"TargetFile": self.target})))
        self.assertTrue(denied_agy(self.pre_tool_agy(orch, "run_command", {"CommandLine": "rm -rf ."})))
        self.assertTrue(denied_agy(self.pre_tool_agy(orch, "run_command", {"CommandLine": "echo x >> app.py"})))
        self.assertEqual(self.pre_tool_agy(orch, "run_command", {"CommandLine": "git status"}), {})
        self.assertEqual(self.pre_tool_agy(orch, "view_file", {"AbsolutePath": self.target}), {})

    def test_workers_and_user_sessions_are_not_restricted(self):
        worker = self.open_worker(self.slug, "fm-api")
        self.assertEqual(self.pre_tool_claude(worker, "Edit", {"file_path": self.target}), {})
        self.assertEqual(self.pre_tool_claude(worker, "Bash", {"command": "echo x > app.py"}), {})


if __name__ == "__main__":
    unittest.main()
