import json
import os
import unittest
from unittest import mock

from tests.helpers import run_cli
from tests.test_delivery_wake import RuntimeCase


def claude(event, sid="cl-1", cwd=None, **extra):
    p = {"session_id": sid, "transcript_path": extra.pop("transcript_path", None), "cwd": cwd,
         "hook_event_name": event}
    p.update(extra)
    return p


class HookCase(RuntimeCase):
    def hook(self, agent, event, payload, env=None):
        from foreman import hooks
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return hooks.run_hook(agent, event, raw, env=env or {})

    def events(self):
        from foreman import paths, store
        return store.read_jsonl(paths.events_file())

    def rec(self, sid):
        return self.sessions.load_session(sid)

    def pending_launch(self, role="worker", title="w", state="starting", agent="claude", **kw):
        """A session as `foreman open` leaves it: terminal known, agent session id not yet."""
        handle = self.term(title)
        rec = self.sessions.create_session(self.slug, agent, model="sonnet", role=role, title=title,
                                           transport={"kind": "fake", "handle": handle}, state=state,
                                           cwd=self.proj["path"], **kw)
        if role == "orchestrator":
            proj = self.projects.load_project(self.slug)
            proj["orchestrator"] = rec["sid"]
            self.projects.save_project(proj)
        return rec, {"ORCA_TERMINAL_HANDLE": handle}


class BindingTest(HookCase):
    def test_garbage_input_gives_empty_json(self):
        self.assertEqual(self.hook("claude", "Stop", "not json at all"), {})
        self.assertEqual(self.hook("claude", "Stop", ""), {})
        self.assertEqual(self.hook("claude", "Stop", "[1,2]"), {})

    def test_unknown_agent_or_event_is_ignored(self):
        self.assertEqual(self.hook("nope", "Stop", {}), {})
        rec, env = self.pending_launch()
        self.assertEqual(self.hook("claude", "PreCompact", claude("PreCompact"), env), {})
        self.assertEqual(self.rec(rec["sid"])["state"], "starting")

    def test_ignored_outside_initialised_projects(self):
        out = self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", cwd=self.make_dir("elsewhere")))
        self.assertEqual(out, {})
        self.assertEqual(self.sessions.list_sessions(), [])
        self.assertEqual(self.events(), [])

    def test_pending_launch_bound_by_terminal_handle(self):
        rec, env = self.pending_launch()
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", cwd=self.proj["path"],
                                                       transcript_path="/t/x.jsonl", prompt="go"), env)
        got = self.rec(rec["sid"])
        self.assertEqual((got["agent_session_id"], got["state"], got["transcript_path"]),
                         ("cl-1", "busy", "/t/x.jsonl"))
        self.assertEqual(len(self.sessions.list_sessions()), 1)
        self.assertIsNotNone(got["last_event_at"])

    def test_known_agent_session_id_wins_without_env(self):
        rec = self.sessions.create_session(self.slug, "claude", agent_session_id="cl-9", state="idle")
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="cl-9", cwd="/nowhere"))
        self.assertEqual(self.rec(rec["sid"])["state"], "busy")

    def test_herdr_pane_env_also_binds(self):
        rec, env = self.pending_launch()
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", cwd=self.proj["path"]),
                  {"HERDR_PANE_ID": env["ORCA_TERMINAL_HANDLE"]})
        self.assertEqual(self.rec(rec["sid"])["agent_session_id"], "cl-1")

    def test_auto_register_user_session(self):
        sub = os.path.join(self.proj["path"], "src")
        os.makedirs(sub)
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="cl-7", cwd=sub,
                                                       transcript_path="/t/7.jsonl"))
        recs = self.sessions.list_sessions(self.slug)
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual((r["owner"], r["role"], r["agent"], r["agent_session_id"], r["state"], r["cwd"]),
                         ("user", "worker", "claude", "cl-7", "busy", sub))
        self.hook("claude", "Stop", claude("Stop", sid="cl-7", cwd=sub))
        self.assertEqual(len(self.sessions.list_sessions(self.slug)), 1)  # second event binds, no duplicate
        self.assertEqual(self.rec(r["sid"])["state"], "idle")

    def test_auto_register_records_terminal_when_known(self):
        handle = self.term("user-term")
        self.hook("claude", "SessionStart", claude("SessionStart", sid="cl-8", cwd=self.proj["path"],
                                                   source="startup"), {"ORCA_TERMINAL_HANDLE": handle})
        r = self.sessions.list_sessions(self.slug)[0]
        self.assertEqual(r["transport"]["handle"], handle)
        self.assertEqual(r["state"], "idle")

    def test_session_start_after_clear_rebinds_same_terminal(self):
        rec, env = self.pending_launch(state="idle", agent_session_id="old")
        self.hook("claude", "SessionStart", claude("SessionStart", sid="new", cwd=self.proj["path"],
                                                   source="clear"), env)
        self.assertEqual(self.rec(rec["sid"])["agent_session_id"], "new")
        self.assertEqual(len(self.sessions.list_sessions()), 1)

    def test_other_agent_in_same_terminal_is_not_bound(self):
        rec, env = self.pending_launch(agent="codex")
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", cwd=self.make_dir("elsewhere")), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "starting")

    def test_rotated_state_is_sticky(self):
        rec, env = self.pending_launch(state="rotated", agent_session_id="cl-1")
        self.hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", cwd=self.proj["path"]), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "rotated")


class StateTest(HookCase):
    def test_transitions(self):
        rec, env = self.pending_launch()
        cwd = self.proj["path"]
        seq = [("SessionStart", "starting"), ("UserPromptSubmit", "busy"), ("PreToolUse", "busy"),
               ("PermissionRequest", "waiting_permission"), ("PostToolUse", "busy"), ("Stop", "idle"),
               ("SessionEnd", "closed")]
        for event, state in seq:
            self.hook("claude", event, claude(event, cwd=cwd, tool_name="Read", reason="other"), env)
            self.assertEqual(self.rec(rec["sid"])["state"], state, event)
        self.assertIsNotNone(self.rec(rec["sid"])["closed_at"])

    def test_notifications(self):
        rec, env = self.pending_launch()
        cwd = self.proj["path"]
        self.hook("claude", "Notification", claude("Notification", cwd=cwd, notification_type="permission_prompt"), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "waiting_permission")
        self.hook("claude", "Notification", claude("Notification", cwd=cwd, notification_type="elicitation_dialog"), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "waiting_input")
        self.hook("claude", "Notification", claude("Notification", cwd=cwd, notification_type="idle_prompt"), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "waiting_input")  # ignored

    def test_session_end_clear_keeps_session(self):
        rec, env = self.pending_launch(state="idle", agent_session_id="cl-1")
        self.hook("claude", "SessionEnd", claude("SessionEnd", cwd=self.proj["path"], reason="clear"), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "idle")

    def test_session_end_with_dead_terminal_is_gone(self):
        rec, env = self.pending_launch(state="idle", agent_session_id="cl-1")
        self.ft.kill(env["ORCA_TERMINAL_HANDLE"])
        self.hook("claude", "SessionEnd", claude("SessionEnd", cwd=self.proj["path"], reason="other"), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "gone")

    def test_agy_events(self):
        rec, env = self.pending_launch(agent="antigravity")
        base = {"conversationId": "ag-1", "workspacePaths": [self.proj["path"]], "modelName": "gemini-x"}
        self.hook("antigravity", "PreInvocation", dict(base, lastUserInput="go"), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "busy")
        self.hook("antigravity", "Stop", dict(base, fullyIdle=False), env)
        self.assertEqual(self.rec(rec["sid"])["state"], "busy")
        self.hook("antigravity", "Stop", dict(base, fullyIdle=True), env)
        got = self.rec(rec["sid"])
        self.assertEqual((got["state"], got["agent_session_id"]), ("idle", "ag-1"))

    def test_codex_events(self):
        rec, env = self.pending_launch(agent="codex")
        self.hook("codex", "UserPromptSubmit", {"session_id": "cx-1", "cwd": self.proj["path"], "model": "gpt-6"}, env)
        self.assertEqual(self.rec(rec["sid"])["state"], "busy")
        self.hook("codex", "Stop", {"session_id": "cx-1", "cwd": self.proj["path"]}, env)
        self.assertEqual(self.rec(rec["sid"])["state"], "idle")

    def test_event_is_logged(self):
        rec, env = self.pending_launch()
        self.hook("claude", "PreToolUse", claude("PreToolUse", cwd=self.proj["path"], tool_name="Bash",
                                                 tool_input={"command": "ls"}), env)
        ev = self.events()[-1]
        self.assertEqual((ev["sid"], ev["agent"], ev["kind"], ev["tool"]), (rec["sid"], "claude", "pre_tool", "Bash"))
        self.assertNotIn("ls", json.dumps(ev.get("command", "")))


class ContextRefreshTest(HookCase):
    def transcript(self, tokens):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {
                "role": "assistant", "model": "claude-x", "content": [{"type": "text", "text": "the final words"}],
                "usage": {"input_tokens": tokens, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0, "output_tokens": 0}}}) + "\n")
        return path

    def test_context_refreshed_on_stop_and_session_start_only(self):
        rec, env = self.pending_launch()
        path = self.transcript(1234)
        self.hook("claude", "PreToolUse", claude("PreToolUse", cwd=self.proj["path"], transcript_path=path,
                                                 tool_name="Read"), env)
        self.assertIsNone(self.rec(rec["sid"])["context_tokens"])
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], transcript_path=path), env)
        got = self.rec(rec["sid"])
        self.assertEqual(got["context_tokens"], 1234)
        self.assertIsNotNone(got["context_at"])

    def test_unreadable_transcript_keeps_known_value(self):
        rec, env = self.pending_launch(state="idle")
        self.sessions.update_session(rec["sid"], context_tokens=777)
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], transcript_path="/nonexistent"), env)
        self.assertEqual(self.rec(rec["sid"])["context_tokens"], 777)

    def test_other_agents_stay_unknown(self):
        rec, env = self.pending_launch(agent="codex")
        self.hook("codex", "Stop", {"session_id": "cx-1", "cwd": self.proj["path"],
                                    "transcript_path": self.transcript(5)}, env)
        self.assertIsNone(self.rec(rec["sid"])["context_tokens"])


class GuardHookTest(HookCase):
    def orch(self, agent="claude"):
        return self.pending_launch(role="orchestrator", title="orch", agent=agent)

    def test_claude_edit_is_denied(self):
        rec, env = self.orch()
        out = self.hook("claude", "PreToolUse", claude("PreToolUse", cwd=self.proj["path"], tool_name="Edit",
                                                       tool_input={"file_path": "a.py"}), env)
        spec = out["hookSpecificOutput"]
        self.assertEqual((spec["hookEventName"], spec["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn("foreman open", spec["permissionDecisionReason"])
        self.assertTrue(self.events()[-1]["blocked"])

    def test_allowed_and_blocked_bash(self):
        rec, env = self.orch()
        p = lambda cmd: self.hook("claude", "PreToolUse", claude(  # noqa: E731
            "PreToolUse", cwd=self.proj["path"], tool_name="Bash", tool_input={"command": cmd}), env)
        self.assertEqual(p("git status"), {})
        self.assertEqual(p("foreman status | head -5"), {})
        self.assertEqual(p("rm -rf build")["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(p("echo x > file.txt")["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_agy_dialect(self):
        rec, env = self.orch("antigravity")
        base = {"conversationId": "ag-1", "workspacePaths": [self.proj["path"]]}
        out = self.hook("antigravity", "PreToolUse", dict(base, toolCall={"name": "write_to_file", "args": {}}), env)
        self.assertEqual(out["decision"], "deny")
        self.assertIn("foreman open", out["reason"])
        out = self.hook("antigravity", "PreToolUse",
                        dict(base, toolCall={"name": "run_command", "args": {"CommandLine": "git log -1"}}), env)
        self.assertEqual(out, {})

    def test_codex_dialect(self):
        rec, env = self.orch("codex")
        out = self.hook("codex", "PreToolUse", {"session_id": "cx-1", "cwd": self.proj["path"],
                                                 "tool_name": "apply_patch", "tool_input": {}}, env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_workers_and_user_sessions_are_never_guarded(self):
        rec, env = self.pending_launch(role="worker")
        out = self.hook("claude", "PreToolUse", claude("PreToolUse", cwd=self.proj["path"], tool_name="Edit"), env)
        self.assertEqual(out, {})


class WakeOnWorkerStopTest(HookCase):
    def setUp(self):
        super().setUp()
        self.orch, _ = self.pending_launch(role="orchestrator", title="orch", state="busy")
        self.w, self.env = self.pending_launch(title="fm-api")

    def test_worker_stop_queues_one_line(self):
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], last_assistant_message="All done.\nTests pass"),
                  self.env)
        pend = self.sessions.inbox_pending(self.orch["sid"])
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["text"], "fm-api (%s, claude/sonnet) finished a turn: All done. Tests pass"
                         % self.w["sid"])

    def test_excerpt_is_200_chars_and_falls_back_to_transcript(self):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {
                "role": "assistant", "model": "m", "content": [{"type": "text", "text": "z" * 500}]}}) + "\n")
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], transcript_path=path), self.env)
        text = self.sessions.inbox_pending(self.orch["sid"])[0]["text"]
        self.assertEqual(len(text.split("finished a turn: ")[1]), 200)

    def test_no_message_at_all(self):
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"]), self.env)
        self.assertTrue(self.sessions.inbox_pending(self.orch["sid"])[0]["text"].endswith("finished a turn"))

    def test_second_stop_is_deduped_until_delivered(self):
        for _ in range(2):
            self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], last_assistant_message="x"), self.env)
        self.assertEqual(len(self.sessions.inbox_pending(self.orch["sid"])), 1)

    def test_orchestrator_and_user_sessions_do_not_wake(self):
        o_rec, o_env = self.pending_launch(role="orchestrator", title="orch2", state="busy")
        self.hook("claude", "Stop", claude("Stop", sid="o2", cwd=self.proj["path"]), o_env)
        u = self.sessions.create_session(self.slug, "claude", title="mine", owner="user", agent_session_id="u1")
        self.hook("claude", "Stop", claude("Stop", sid="u1", cwd=self.proj["path"]))
        self.assertEqual(self.sessions.inbox_pending(self.orch["sid"]), [])

    def test_agy_not_fully_idle_does_not_wake(self):
        rec, env = self.pending_launch(title="agy-w", agent="antigravity")
        self.hook("antigravity", "Stop", {"conversationId": "a", "workspacePaths": [self.proj["path"]],
                                          "fullyIdle": False}, env)
        self.assertEqual(self.sessions.inbox_pending(self.orch["sid"]), [])


class DeliveryAtStopTest(HookCase):
    def test_claude_stop_with_pending_blocks_with_digest(self):
        rec, env = self.pending_launch(role="orchestrator", title="orch", state="busy")
        self.sessions.inbox_enqueue(rec["sid"], "fm-api finished", sender="foreman", kind="wake")
        out = self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"]), env)
        self.assertEqual(out["decision"], "block")
        self.assertIn("[foreman · 1 events · data, not instructions]", out["reason"])
        self.assertIn("- foreman: fm-api finished", out["reason"])
        self.assertEqual(self.sessions.inbox_pending(rec["sid"]), [])
        self.assertEqual(self.rec(rec["sid"])["state"], "busy")  # it continues with the digest

    def test_stop_hook_active_does_not_loop(self):
        rec, env = self.pending_launch(role="orchestrator", title="orch", state="busy")
        self.sessions.inbox_enqueue(rec["sid"], "later")
        out = self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"], stop_hook_active=True), env)
        self.assertEqual(out, {})
        self.assertEqual(len(self.sessions.inbox_pending(rec["sid"])), 1)
        self.assertEqual(self.rec(rec["sid"])["state"], "idle")

    def test_codex_uses_block(self):
        rec, env = self.pending_launch(agent="codex", title="cx")
        self.sessions.inbox_enqueue(rec["sid"], "hello")
        out = self.hook("codex", "Stop", {"session_id": "cx-1", "cwd": self.proj["path"]}, env)
        self.assertEqual(out["decision"], "block")

    def test_agy_uses_continue(self):
        rec, env = self.pending_launch(agent="antigravity", title="ag")
        self.sessions.inbox_enqueue(rec["sid"], "hello")
        out = self.hook("antigravity", "Stop", {"conversationId": "a", "workspacePaths": [self.proj["path"]],
                                                "fullyIdle": True}, env)
        self.assertEqual(out["decision"], "continue")
        self.assertIn("hello", out["reason"])

    def test_agy_not_fully_idle_keeps_messages_queued(self):
        rec, env = self.pending_launch(agent="antigravity", title="ag")
        self.sessions.inbox_enqueue(rec["sid"], "hello")
        out = self.hook("antigravity", "Stop", {"conversationId": "a", "workspacePaths": [self.proj["path"]],
                                                "fullyIdle": False}, env)
        self.assertEqual(out, {})
        self.assertEqual(len(self.sessions.inbox_pending(rec["sid"])), 1)

    def test_nothing_pending_nothing_printed(self):
        rec, env = self.pending_launch()
        self.assertEqual(self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"]), env), {})


class RobustnessTest(HookCase):
    def test_internal_error_is_logged_and_output_is_empty_json(self):
        rec, env = self.pending_launch()
        with mock.patch("foreman.hooks._process", side_effect=RuntimeError("boom")):
            out = self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"]), env)
        self.assertEqual(out, {})
        err = self.events()[-1]
        self.assertEqual(err["kind"], "hook_error")
        self.assertIn("boom", err["error"])

    def test_failing_wake_does_not_break_the_hook(self):
        rec, env = self.pending_launch()
        with mock.patch("foreman.wake.queue_for_orchestrator", side_effect=RuntimeError("nope")):
            out = self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"]), env)
        self.assertEqual(out, {})
        self.assertEqual(self.rec(rec["sid"])["state"], "idle")

    def test_events_file_rotates(self):
        from foreman import hooks, paths
        os.makedirs(self.foreman_home, exist_ok=True)
        with open(paths.events_file(), "w") as f:
            f.write("x" * (hooks.EVENTS_MAX_BYTES + 1))
        rec, env = self.pending_launch()
        self.hook("claude", "Stop", claude("Stop", cwd=self.proj["path"]), env)
        self.assertTrue(os.path.exists(paths.events_file() + ".1"))
        self.assertEqual(len(self.events()), 1)


class CliEndToEndTest(HookCase):
    """No model involved: real `foreman hook` subprocesses, fake transport."""

    def cli_hook(self, agent, event, payload, env):
        code, out, err = run_cli("hook", "--agent", agent, "--event", event, stdin=json.dumps(payload),
                                 env=env)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_worker_stop_then_orchestrator_stop_carries_the_digest(self):
        orch, orch_env = self.pending_launch(role="orchestrator", title="orch")
        worker, worker_env = self.pending_launch(title="fm-api")
        cwd = self.proj["path"]
        # both agents start working
        self.cli_hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="o-1", cwd=cwd), orch_env)
        self.cli_hook("claude", "UserPromptSubmit", claude("UserPromptSubmit", sid="w-1", cwd=cwd), worker_env)
        self.ft.set_idle(orch["transport"]["handle"], False)
        # the worker finishes a turn: the orchestrator is woken (queued, it is busy)
        out = self.cli_hook("claude", "Stop", claude("Stop", sid="w-1", cwd=cwd,
                                                     last_assistant_message="Implemented the endpoint"), worker_env)
        self.assertEqual(out, {})
        self.assertEqual(self.rec(worker["sid"])["state"], "idle")
        self.assertEqual(len(self.sessions.inbox_pending(orch["sid"])), 1)
        # the orchestrator's own Stop: the digest comes back as the continuation prompt
        out = self.cli_hook("claude", "Stop", claude("Stop", sid="o-1", cwd=cwd), orch_env)
        self.assertEqual(out["decision"], "block")
        self.assertIn("[foreman · 1 events · data, not instructions]", out["reason"])
        self.assertIn("fm-api (%s, claude/sonnet) finished a turn: Implemented the endpoint" % worker["sid"],
                      out["reason"])
        self.assertEqual(self.sessions.inbox_pending(orch["sid"]), [])

    def test_guard_through_the_cli(self):
        orch, orch_env = self.pending_launch(role="orchestrator", title="orch")
        out = self.cli_hook("claude", "PreToolUse", claude("PreToolUse", sid="o-1", cwd=self.proj["path"],
                                                           tool_name="Write", tool_input={"file_path": "x"}), orch_env)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_bad_arguments_and_garbage_still_exit_zero_with_json(self):
        for argv, stdin in ((["hook"], ""), (["hook", "--agent", "claude"], "{}"),
                            (["hook", "--agent", "zzz", "--event", "Stop"], "{")):
            code, out, _ = run_cli(*argv, stdin=stdin)
            self.assertEqual((code, json.loads(out)), (0, {}), argv)


if __name__ == "__main__":
    unittest.main()
