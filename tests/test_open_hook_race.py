"""Regression (found by live E2): the agent starts working inside `orca agent launch`, so its first hook
(SessionStart) can arrive before `foreman open` has recorded the session. The hook then auto-registers a
user-owned *worker* on the same terminal; `open` must absorb it, otherwise an orchestrator runs unguarded."""
import json
import os
from unittest import mock

from tests.test_delivery_wake import RuntimeCase
from foreman.cliutil import CliError  # noqa: E402


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


class PendingOrchestratorMarkerTest(RuntimeCase):
    """D54: close the window between `transport.launch` returning and the record being created. Before launching an
    orchestrator, `open` writes a pending-orchestrator marker; while it is younger than 180 s, a hook from an UNKNOWN
    session in that project's cwd binds as the pending orchestrator (guard active) and is absorbed into the real
    record afterwards."""

    def _hook(self, event, payload, handle=None, session="cl-pend", cwd=None):
        from foreman import hooks
        payload = dict({"session_id": session, "cwd": cwd or self.proj["path"], "hook_event_name": event}, **payload)
        env = {"FOREMAN_TERMINAL_HANDLE": handle} if handle else {}
        return hooks.run_hook("claude", event, json.dumps(payload), env=env)

    WRITE = {"tool_name": "Write", "tool_input": {"file_path": "x", "content": "y"}}

    def _open_orchestrator(self, during_launch, fail=False):
        """Run open_session(role=orchestrator); `during_launch(handle)` fires hooks while the record does not exist."""
        from foreman.commands import open as open_cmd
        from foreman.transports.fake import FakeTransport
        real_launch = FakeTransport.launch
        seen = {}

        def racing_launch(ft, *a, **kw):
            handle = real_launch(ft, *a, **kw)
            seen["marker"] = self.sessions.pending_orchestrator(self.slug)
            seen["out"] = during_launch(handle)
            if fail:
                raise open_cmd.TransportError("boom")
            return handle

        with mock.patch.object(FakeTransport, "launch", racing_launch):
            try:
                rec, _ = open_cmd.open_session(
                    project=self.proj, purpose="orchestrator", prompt="brief", title="demo-orch", kind="orchestrator",
                    agent="claude", model="claude-haiku-5-5", transport_kind="fake", owner="user",
                    role="orchestrator")
            except CliError:
                rec = None
        return rec, seen

    def test_session_start_and_write_before_the_record_exists_are_guarded(self):
        def early(handle):
            start = self._hook("SessionStart", {"source": "startup"}, handle)
            write = self._hook("PreToolUse", self.WRITE, handle)
            return start, write
        rec, seen = self._open_orchestrator(early)
        start, write = seen["out"]
        self.assertEqual(write["hookSpecificOutput"]["permissionDecision"], "deny")  # denied BEFORE the record exists
        # the early hooks created no lasting worker: one open session, the orchestrator, with the early activity
        open_recs = [r for r in self.sessions.list_sessions() if self.sessions.is_open(r)]
        self.assertEqual([r["sid"] for r in open_recs], [rec["sid"]])
        self.assertEqual((open_recs[0]["role"], open_recs[0]["agent_session_id"]), ("orchestrator", "cl-pend"))
        self.assertNotIn("pending_orchestrator", open_recs[0])
        # and it stays guarded afterwards
        out = self._hook("PreToolUse", self.WRITE, rec["transport"]["handle"])
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_marker_content_and_removal_on_success(self):
        rec, seen = self._open_orchestrator(lambda h: None)
        marker = seen["marker"]
        self.assertEqual((marker["agent"], marker["cwd"], marker["pid"]), ("claude", self.proj["path"], os.getpid()))
        self.assertIn("created_at", marker)
        self.assertIsNone(self.sessions.pending_orchestrator(self.slug))

    def test_marker_removed_and_stray_demoted_when_the_launch_fails(self):
        rec, seen = self._open_orchestrator(lambda h: self._hook("SessionStart", {"source": "startup"}, h), fail=True)
        self.assertIsNone(rec)
        self.assertIsNone(self.sessions.pending_orchestrator(self.slug))
        # review 4 N1: not closed (it may be the user's own terminal) but a plain, unguarded user worker again
        left = [r for r in self.sessions.list_sessions() if self.sessions.is_open(r)]
        self.assertEqual([(r["role"], r["owner"], "pending_orchestrator" in r) for r in left],
                         [("worker", "user", False)])

    def test_a_marker_older_than_180_seconds_is_ignored(self):
        self.sessions.mark_pending_orchestrator(self.slug, "claude", self.proj["path"])
        os.environ["FOREMAN_NOW"] = "2099-01-01T00:00:00Z"
        self.addCleanup(os.environ.pop, "FOREMAN_NOW", None)
        self.assertIsNone(self.sessions.pending_orchestrator(self.slug))
        out = self._hook("PreToolUse", self.WRITE, "fake-h1")  # unknown session: a plain user worker, not guarded
        self.assertEqual(out, {})
        self.assertEqual([r["role"] for r in self.sessions.list_sessions()], ["worker"])

    def test_a_dead_launcher_pid_makes_the_marker_stale(self):
        self.sessions.mark_pending_orchestrator(self.slug, "claude", self.proj["path"], pid=2 ** 22 + 12345)
        self.assertIsNone(self.sessions.pending_orchestrator(self.slug))

    def test_other_projects_and_other_agents_are_not_captured(self):
        other = self.init_project("elsewhere")
        self.sessions.mark_pending_orchestrator(self.slug, "claude", self.proj["path"])
        out = self._hook("PreToolUse", self.WRITE, "fake-h2", cwd=other["path"])  # another project's folder
        self.assertEqual(out, {})
        from foreman import hooks
        hooks.run_hook("antigravity", "PreToolUse", json.dumps(
            {"conversationId": "agy-1", "cwd": self.proj["path"], "toolCall": {"name": "write_to_file"}}), env={})
        roles = [r["role"] for r in self.sessions.list_sessions()]
        self.assertNotIn("orchestrator", roles)  # neither the other project nor another agent kind was captured

    def test_a_second_orchestrator_launch_is_refused_while_one_is_pending(self):
        from foreman.commands import open as open_cmd
        self.sessions.mark_pending_orchestrator(self.slug, "claude", self.proj["path"], pid=os.getppid())
        with self.assertRaises(CliError):
            open_cmd.open_session(project=self.proj, purpose="o", prompt="b", kind="orchestrator", agent="claude",
                                  transport_kind="fake", owner="user", role="orchestrator")
