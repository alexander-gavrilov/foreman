import json
import unittest

from tests.helpers import ForemanTestCase  # noqa: F401  (puts lib on sys.path)


CLAUDE_PRE = {"session_id": "cl-1", "transcript_path": "/t/cl-1.jsonl", "cwd": "/w/demo",
              "hook_event_name": "PreToolUse", "permission_mode": "default", "tool_name": "Bash",
              "tool_input": {"command": "ls -la"}, "tool_use_id": "toolu_1"}
CLAUDE_STOP = {"session_id": "cl-1", "transcript_path": "/t/cl-1.jsonl", "cwd": "/w/demo",
               "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": "Done: tests pass",
               "background_tasks": [], "session_crons": []}
AGY_PRE = {"conversationId": "ag-1", "workspacePaths": ["/w/demo"], "transcriptPath": "/t/ag/transcript_full.jsonl",
           "modelName": "gemini-3.8-flash-low", "lastUserInput": "fix it", "stepIdx": 4,
           "toolCall": {"name": "run_command", "args": {"CommandLine": "git status"}}}
AGY_STOP = {"conversationId": "ag-1", "workspacePaths": ["/w/demo"], "transcriptPath": "/t/ag/t.jsonl",
            "modelName": "m", "executionNum": 1, "terminationReason": "NO_TOOL_CALL", "fullyIdle": True}
CODEX_PRE = {"session_id": "cx-1", "transcript_path": None, "cwd": "/w/demo", "hook_event_name": "PreToolUse",
             "model": "gpt-6", "permission_mode": "default", "turn_id": "t1", "tool_name": "apply_patch",
             "tool_input": {"input": "*** Begin Patch"}, "tool_use_id": "c1"}


class ClaudeAdapterTest(unittest.TestCase):
    def setUp(self):
        from foreman.adapters import claude
        self.a = claude

    def n(self, event, payload):
        return self.a.normalise(event, payload)

    def test_event_kinds(self):
        for native, kind in (("SessionStart", "session_start"), ("UserPromptSubmit", "prompt"),
                             ("PreToolUse", "pre_tool"), ("PostToolUse", "post_tool"), ("Stop", "stop"),
                             ("PermissionRequest", "wait_permission"), ("SessionEnd", "session_end")):
            self.assertEqual(self.n(native, dict(CLAUDE_PRE))["kind"], kind, native)

    def test_pre_tool_fields(self):
        n = self.n("PreToolUse", CLAUDE_PRE)
        self.assertEqual(n["agent_session_id"], "cl-1")
        self.assertEqual(n["cwd"], "/w/demo")
        self.assertEqual(n["transcript_path"], "/t/cl-1.jsonl")
        self.assertEqual(n["tool_name"], "Bash")
        self.assertEqual(n["tool_input"], {"command": "ls -la"})
        self.assertEqual(n["command"], "ls -la")

    def test_stop_fields(self):
        n = self.n("Stop", CLAUDE_STOP)
        self.assertEqual(n["kind"], "stop")
        self.assertEqual(n["last_assistant_message"], "Done: tests pass")
        self.assertFalse(n["stop_hook_active"])
        self.assertTrue(self.n("Stop", dict(CLAUDE_STOP, stop_hook_active=True))["stop_hook_active"])

    def test_notification_types(self):
        base = {"session_id": "cl-1", "cwd": "/w", "hook_event_name": "Notification"}
        self.assertEqual(self.n("Notification", dict(base, notification_type="permission_prompt"))["kind"],
                         "wait_permission")
        self.assertEqual(self.n("Notification", dict(base, notification_type="elicitation_dialog"))["kind"],
                         "wait_input")
        self.assertIsNone(self.n("Notification", dict(base, notification_type="idle_prompt"))["kind"])
        self.assertIsNone(self.n("Notification", dict(base, notification_type="auth_success"))["kind"])
        self.assertEqual(self.n("Notification", dict(base, message="Claude needs your permission to use Bash"))["kind"],
                         "wait_permission")

    def test_session_end_reason_and_unknown_event(self):
        n = self.n("SessionEnd", {"session_id": "x", "reason": "clear"})
        self.assertEqual(n["kind"], "session_end")
        self.assertEqual(n["reason"], "clear")
        self.assertIsNone(self.n("PreCompact", {"session_id": "x"})["kind"])

    def test_missing_fields_are_none(self):
        n = self.n("Stop", {})
        self.assertIsNone(n["agent_session_id"])
        self.assertIsNone(n["cwd"])

    def test_render(self):
        self.assertEqual(self.a.render_deny("no"), {"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "no"}})
        self.assertEqual(self.a.render_continue("digest"), {"decision": "block", "reason": "digest"})


class AntigravityAdapterTest(unittest.TestCase):
    def setUp(self):
        from foreman.adapters import antigravity
        self.a = antigravity

    def test_kinds(self):
        self.assertEqual(self.a.normalise("SessionStart", AGY_PRE)["kind"], "session_start")
        self.assertEqual(self.a.normalise("PreInvocation", AGY_PRE)["kind"], "prompt")
        self.assertEqual(self.a.normalise("PreToolUse", AGY_PRE)["kind"], "pre_tool")
        self.assertEqual(self.a.normalise("PostToolUse", AGY_PRE)["kind"], "post_tool")
        self.assertEqual(self.a.normalise("Stop", AGY_STOP)["kind"], "stop")
        self.assertIsNone(self.a.normalise("PostInvocation", AGY_PRE)["kind"])
        self.assertIsNone(self.a.normalise("UserPromptSubmit", AGY_PRE)["kind"])

    def test_fields(self):
        n = self.a.normalise("PreToolUse", AGY_PRE)
        self.assertEqual(n["agent_session_id"], "ag-1")
        self.assertEqual(n["cwd"], "/w/demo")
        self.assertEqual(n["transcript_path"], "/t/ag/transcript_full.jsonl")
        self.assertEqual(n["model"], "gemini-3.8-flash-low")
        self.assertEqual(n["tool_name"], "run_command")
        self.assertEqual(n["command"], "git status")

    def test_stop_fully_idle(self):
        self.assertTrue(self.a.normalise("Stop", AGY_STOP)["fully_idle"])
        self.assertFalse(self.a.normalise("Stop", dict(AGY_STOP, fullyIdle=False))["fully_idle"])

    def test_render(self):
        self.assertEqual(self.a.render_deny("no"), {"decision": "deny", "reason": "no"})
        self.assertEqual(self.a.render_continue("d"), {"decision": "continue", "reason": "d"})


class CodexAdapterTest(unittest.TestCase):
    def setUp(self):
        from foreman.adapters import codex
        self.a = codex

    def test_kinds_and_fields(self):
        n = self.a.normalise("PreToolUse", CODEX_PRE)
        self.assertEqual((n["kind"], n["agent_session_id"], n["model"], n["tool_name"]),
                         ("pre_tool", "cx-1", "gpt-6", "apply_patch"))
        self.assertEqual(self.a.normalise("PermissionRequest", CODEX_PRE)["kind"], "wait_permission")
        self.assertEqual(self.a.normalise("Stop", dict(CLAUDE_STOP, session_id="cx-1"))["kind"], "stop")
        self.assertEqual(self.a.normalise("UserPromptSubmit", {"session_id": "cx-1", "prompt": "hi"})["prompt"], "hi")
        self.assertIsNone(self.a.normalise("PreCompact", CODEX_PRE)["kind"])

    def test_shell_command_forms(self):
        n = self.a.normalise("PreToolUse", {"session_id": "c", "tool_name": "shell",
                                            "tool_input": {"command": ["bash", "-lc", "ls"]}})
        self.assertEqual(n["command"], "ls")
        n = self.a.normalise("PreToolUse", {"session_id": "c", "tool_name": "shell",
                                            "tool_input": {"command": ["git", "status"]}})
        self.assertEqual(n["command"], "git status")

    def test_render(self):
        self.assertEqual(self.a.render_deny("no")["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.a.render_continue("d"), {"decision": "block", "reason": "d"})


class RegistryTest(unittest.TestCase):
    def test_get(self):
        from foreman import adapters
        for name in ("claude", "antigravity", "codex"):
            self.assertTrue(hasattr(adapters.get(name), "normalise"))
        with self.assertRaises(ValueError):
            adapters.get("nope")


if __name__ == "__main__":
    unittest.main()
