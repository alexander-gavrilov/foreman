"""Fix round 6, bug A (D59): a child agent session started inside a registered terminal (a `claude -p` from an
unrelated plugin hook inherits the terminal handle) must not take over the record."""
import json

from tests.test_hooks import HookCase, claude


def start(sid, cwd, source="startup", **extra):
    return claude("SessionStart", sid=sid, cwd=cwd, source=source, model="claude-opus-5-5",
                  transcript_path="/tmp/%s.jsonl" % sid, **extra)


class ChildSessionTest(HookCase):
    def setUp(self):
        super().setUp()
        self.orch, self.env = self.pending_launch(role="orchestrator", title="orch", state="idle",
                                                  agent_session_id="orch-1", transcript_path="/tmp/orch-1.jsonl")
        self.cwd = self.proj["path"]
        self.sessions.inbox_enqueue(self.orch["sid"], "from a worker", sender="w")

    def child_events(self):
        return [e for e in self.events() if e.get("kind") == "child_session"]

    def test_child_start_tool_stop_end_leave_the_orchestrator_alone(self):
        sid = self.orch["sid"]
        before = self.rec(sid)
        out = self.hook("claude", "SessionStart", start("kid-1", self.cwd, entrypoint="sdk-cli"), self.env)
        self.assertNotIn("hookSpecificOutput", out)  # no briefing for a child
        self.assertEqual(self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="kid-1", cwd=self.cwd,
                                                                         prompt="x"), self.env), {})
        read = self.hook("claude", "PreToolUse", claude("PreToolUse", sid="kid-1", cwd=self.cwd, tool_name="Read",
                                                        tool_input={"file_path": "/etc/hostname"}), self.env)
        self.assertNotEqual(read.get("hookSpecificOutput", {}).get("permissionDecision"), "deny")
        self.hook("claude", "PostToolUse", claude("PostToolUse", sid="kid-1", cwd=self.cwd, tool_name="Read"), self.env)
        stop = self.hook("claude", "Stop", claude("Stop", sid="kid-1", cwd=self.cwd,
                                                  last_assistant_message="done"), self.env)
        self.assertEqual(stop, {})  # nothing claimed
        self.assertEqual([m["claimed_by"] if "claimed_by" in m else None
                          for m in self.sessions.inbox_pending(sid)], [None])
        self.hook("claude", "SessionEnd", claude("SessionEnd", sid="kid-1", cwd=self.cwd, reason="other"), self.env)
        after = self.rec(sid)
        for key in ("agent_session_id", "state", "transcript_path", "lifecycle", "context_tokens",
                    "past_agent_session_ids", "closed_at", "last_event_at"):
            self.assertEqual(after.get(key), before.get(key), key)
        self.assertEqual(len(self.sessions.list_sessions()), 1)
        self.assertEqual(len(self.sessions.inbox_pending(sid)), 1)

    def test_one_child_session_event_per_child_id(self):
        for event in ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd"):
            self.hook("claude", event, claude(event, sid="kid-1", cwd=self.cwd, source="startup", reason="other"),
                      self.env)
        self.hook("claude", "Stop", claude("Stop", sid="kid-2", cwd=self.cwd), self.env)
        ids = [e.get("agent_session_id") for e in self.child_events()]
        self.assertEqual(sorted(ids), ["kid-1", "kid-2"])
        self.assertEqual([e for e in self.events() if e.get("kind") != "child_session" and e.get("kind") != "pre_tool"],
                         [])  # no ordinary event row is written for a child's hooks

    def test_child_write_is_denied(self):
        out = self.hook("claude", "PreToolUse", claude("PreToolUse", sid="kid-1", cwd=self.cwd, tool_name="Write",
                                                       tool_input={"file_path": "x", "content": "y"}), self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.rec(self.orch["sid"])["agent_session_id"], "orch-1")

    def test_child_shell_command_goes_through_the_guard(self):
        out = self.hook("claude", "PreToolUse", claude("PreToolUse", sid="kid-1", cwd=self.cwd, tool_name="Bash",
                                                       tool_input={"command": "rm -rf x"}), self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_guard_failure_for_a_child_fails_closed(self):
        from unittest import mock
        with mock.patch("foreman.guard.check_tool", side_effect=RuntimeError("boom")):
            out = self.hook("claude", "PreToolUse", claude("PreToolUse", sid="kid-1", cwd=self.cwd,
                                                           tool_name="Read"), self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_clear_still_rebinds_and_the_new_session_is_guarded(self):
        self.hook("claude", "SessionEnd", claude("SessionEnd", sid="orch-1", cwd=self.cwd, reason="clear"), self.env)
        self.hook("claude", "SessionStart", start("orch-2", self.cwd, source="clear"), self.env)
        got = self.rec(self.orch["sid"])
        self.assertEqual((got["agent_session_id"], got["past_agent_session_ids"]), ("orch-2", ["orch-1"]))
        self.assertEqual(self.child_events(), [])
        out = self.hook("claude", "PreToolUse", claude("PreToolUse", sid="orch-2", cwd=self.cwd, tool_name="Write"),
                        self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_resume_and_compact_rebind_too(self):
        for n, source in enumerate(("resume", "compact")):
            self.hook("claude", "SessionStart", start("next-%d" % n, self.cwd, source=source), self.env)
            self.assertEqual(self.rec(self.orch["sid"])["agent_session_id"], "next-%d" % n)

    def test_a_post_clear_id_without_session_start_is_denied_but_not_bound(self):
        out = self.hook("claude", "PreToolUse", claude("PreToolUse", sid="orch-2", cwd=self.cwd, tool_name="Edit"),
                        self.env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(self.rec(self.orch["sid"])["agent_session_id"], "orch-1")

    def test_a_child_startup_never_rebinds_even_in_the_same_second(self):
        self.hook("claude", "SessionStart", start("kid-1", self.cwd, source="startup"), self.env)
        self.assertEqual(self.rec(self.orch["sid"])["agent_session_id"], "orch-1")

    def test_a_child_with_the_sdk_entrypoint_never_counts_as_a_successor_even_on_resume(self):
        env = dict(self.env, CLAUDE_CODE_ENTRYPOINT="sdk-cli")
        self.hook("claude", "SessionStart", start("kid-1", self.cwd, source="resume"), env)
        self.assertEqual(self.rec(self.orch["sid"])["agent_session_id"], "orch-1")
        self.assertEqual(len(self.child_events()), 1)

    def test_the_old_id_still_finds_the_record_after_a_rebind(self):
        self.hook("claude", "SessionStart", start("orch-2", self.cwd, source="clear"), self.env)
        self.hook("claude", "Stop", claude("Stop", sid="orch-1", cwd=self.cwd), {})  # late hook of the old id
        self.assertEqual(self.child_events(), [])

    def test_a_record_without_an_agent_session_id_is_claimed_by_the_first_hook(self):
        rec, env = self.pending_launch(title="w2")
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="w-1", cwd=self.cwd), env)
        self.assertEqual(self.rec(rec["sid"])["agent_session_id"], "w-1")

    def test_a_worker_child_changes_nothing_and_wakes_nobody(self):
        w, wenv = self.pending_launch(title="fm-api", state="busy", agent_session_id="w-1")
        self.sessions.inbox_claim(self.orch["sid"], claimer="x")  # empty the orchestrator's queue first
        self.hook("claude", "Stop", claude("Stop", sid="kid-9", cwd=self.cwd, last_assistant_message="child"), wenv)
        self.assertEqual(self.rec(w["sid"])["state"], "busy")
        self.assertEqual(self.sessions.inbox_pending(self.orch["sid"]), [])
        self.hook("claude", "SessionEnd", claude("SessionEnd", sid="kid-9", cwd=self.cwd, reason="other"), wenv)
        self.assertEqual(self.rec(w["sid"])["lifecycle"], "active")

    def test_agy_without_a_source_keeps_the_handle_rebind(self):
        rec, env = self.pending_launch(agent="antigravity", title="agy", state="idle", agent_session_id="conv-1")
        self.hook("antigravity", "PreInvocation", {"conversationId": "conv-2", "workspacePaths": [self.cwd],
                                                   "cwd": self.cwd}, env)
        self.assertEqual(self.rec(rec["sid"])["agent_session_id"], "conv-2")
        self.assertEqual(self.child_events(), [])

    def test_a_json_event_row_is_plain_data(self):
        self.hook("claude", "Stop", claude("Stop", sid="kid-1", cwd=self.cwd), self.env)
        row = self.child_events()[0]
        json.dumps(row)
        self.assertEqual(row["sid"], self.orch["sid"])
