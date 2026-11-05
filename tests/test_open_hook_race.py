"""Regression (found by live E2): the agent starts working inside `orca agent launch`, so its first hook
(SessionStart) can arrive before `foreman open` has recorded the session. The hook then auto-registers a
user-owned *worker* on the same terminal; `open` must absorb it, otherwise an orchestrator runs unguarded."""
import json
import os
from unittest import mock

from tests.test_delivery_wake import RuntimeCase


class OpenHookRaceTest(RuntimeCase):
    def _hook(self, event, payload, handle):
        from foreman import hooks
        payload = dict({"session_id": "cl-race", "cwd": self.proj["path"], "hook_event_name": event}, **payload)
        return hooks.run_hook("claude", event, json.dumps(payload), env={"FOREMAN_TERMINAL_HANDLE": handle})

    def _open_orchestrator(self):
        from foreman.commands import open as open_cmd
        from foreman.transports.fake import FakeTransport
        real_launch = FakeTransport.launch
        test = self

        def racing_launch(ft, *a, **kw):
            handle = real_launch(ft, *a, **kw)
            test._hook("SessionStart", {"source": "startup", "transcript_path": "/t/race.jsonl"}, handle)
            test._hook("UserPromptSubmit", {"prompt": "go"}, handle)
            return handle

        with mock.patch.object(FakeTransport, "launch", racing_launch):
            rec, _ = open_cmd.open_session(
                project=self.proj, purpose="orchestrator", prompt="brief", title="demo-orch", kind="orchestrator",
                agent="claude", model="claude-haiku-5-5", transport_kind="fake", owner="user", role="orchestrator")
        return rec

    def test_open_absorbs_the_session_a_fast_hook_auto_registered(self):
        rec = self._open_orchestrator()
        open_recs = [r for r in self.sessions.list_sessions() if self.sessions.is_open(r)]
        self.assertEqual([r["sid"] for r in open_recs], [rec["sid"]])
        got = open_recs[0]
        self.assertEqual((got["role"], got["agent_session_id"], got["transcript_path"], got["state"]),
                         ("orchestrator", "cl-race", "/t/race.jsonl", "busy"))

    def test_later_hooks_bind_to_the_orchestrator_and_the_guard_applies(self):
        rec = self._open_orchestrator()
        out = self._hook("PreToolUse", {"tool_name": "Write", "tool_input": {"file_path": "x", "content": "y"}},
                         rec["transport"]["handle"])
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(len([r for r in self.sessions.list_sessions() if self.sessions.is_open(r)]), 1)
