"""Fix round 10: agy transcript reading in context.last_assistant_text (D63 fix),
and D64 false busy reconciliation in the watch pass.
"""
import json
import os
import unittest
from unittest import mock

from tests.helpers import ForemanTestCase
from tests.test_hooks import HookCase
from foreman import context, messaging, paths, store, watch


class AgyTranscriptContextTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "transcript_full.jsonl")

    def write_rows(self, rows):
        with open(self.path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write((json.dumps(r) if isinstance(r, dict) else r) + "\n")

    def test_reads_agy_planner_response_with_exact_shapes(self):
        rows = [
            {"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "check status"},
            {"source": "SYSTEM_SDK", "type": "EPHEMERAL_MESSAGE", "content": "ephemeral notice"},
            {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE",
             "created_at": "2026-11-06T12:00:00Z", "content": "tool result text"},
            {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "created_at": "2026-11-06T12:00:01Z", "output_tokens": 10,
             "tool_calls": [{"name": "read_file", "args": {"Path": "x"}}]},
            {"source": "SYSTEM_SDK", "type": "EPHEMERAL_MESSAGE", "content": "another notice"},
            {"step_index": 4, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "created_at": "2026-11-06T12:00:05Z", "output_tokens": 52,
             "content": "All tests passed successfully.\nReady to deploy."},
            {"source": "SYSTEM_SDK", "type": "EPHEMERAL_MESSAGE", "content": "trailing system message"},
        ]
        self.write_rows(rows)
        self.assertEqual(
            context.last_assistant_text(self.path),
            "All tests passed successfully. Ready to deploy."
        )

    def test_only_model_planner_response_rows_with_non_empty_text_count(self):
        rows = [
            {"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "text in user input"},
            {"source": "SYSTEM_SDK", "type": "EPHEMERAL_MESSAGE", "content": "system text"},
            {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE",
             "content": "generic model text"},
            {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "output_tokens": 20, "tool_calls": [{"name": "bash"}]},  # no content
            {"step_index": 4, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "output_tokens": 0, "content": "   \n  \t  "},  # empty/whitespace content
        ]
        self.write_rows(rows)
        self.assertIsNone(context.last_assistant_text(self.path))


class AgyStopHookTest(HookCase):
    def setUp(self):
        super().setUp()
        self.tpath = os.path.join(self.tmp, "agy_transcript.jsonl")

    def write_agy_transcript(self, text):
        rows = [
            {"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "user question"},
            {"source": "SYSTEM_SDK", "type": "EPHEMERAL_MESSAGE", "content": "system start"},
            {"step_index": 4, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE",
             "created_at": "2026-11-06T12:00:00Z", "output_tokens": 52, "content": text},
        ]
        with open(self.tpath, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_orchestrator_stop_posts_agy_transcript_text(self):
        orch, env = self.pending_launch(
            role="orchestrator", title="agy-orch", agent="antigravity",
            agent_session_id="agy-orch-conv-1", state="idle", transcript_path=self.tpath
        )
        self.write_agy_transcript("Я всё проверил, сборка прошла успешно.")
        # User message arrives, marking orchestrator awaiting_reply
        messaging.user_reply(self.slug, "Как дела со сборкой?", channel="web")
        self.assertTrue(self.rec(orch["sid"]).get("awaiting_reply"))

        # Antigravity Stop hook fires
        stop_payload = {
            "conversationId": "agy-orch-conv-1",
            "workspacePaths": [self.proj["path"]],
            "transcriptPath": self.tpath,
            "fullyIdle": True,
        }
        out = self.hook("antigravity", "Stop", stop_payload, env)
        self.assertEqual(out, {})
        posted = [m for m in self.projects.chat_list(self.slug) if m["from"] == "orchestrator"]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["text"], "Я всё проверил, сборка прошла успешно.")
        # Ensure "no answer" notice was NOT posted
        notices = [m for m in self.projects.chat_list(self.slug) if m["from"] == "foreman"]
        self.assertEqual(len(notices), 0)

    def test_worker_stop_summary_carries_agy_transcript_text(self):
        orch, _ = self.pending_launch(role="orchestrator", title="orch", agent="claude", state="idle")
        worker, env = self.pending_launch(
            role="worker", title="agy-worker", agent="antigravity",
            agent_session_id="agy-worker-conv-1", state="busy", transcript_path=self.tpath
        )
        self.sessions.update_session(worker["sid"], model="gemini")
        self.write_agy_transcript("Fix 10 implemented and verified.")

        stop_payload = {
            "conversationId": "agy-worker-conv-1",
            "workspacePaths": [self.proj["path"]],
            "transcriptPath": self.tpath,
            "fullyIdle": True,
        }
        self.hook("antigravity", "Stop", stop_payload, env)

        pending = self.sessions.inbox_pending(orch["sid"])
        self.assertEqual(len(pending), 1)
        expected_prefix = "%s (%s, antigravity/gemini) finished a turn: Fix 10 implemented and verified." % (
            worker["title"], worker["sid"]
        )
        self.assertEqual(pending[0]["text"], expected_prefix)


class FalseBusyReconciliationTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        os.environ["FOREMAN_NOW"] = "2026-11-06T12:00:00Z"
        self.init_project("demo")
        from foreman import transports
        self.fake_t = transports.get_transport("fake")

    def _events_of_kind(self, kind):
        return [e for e in store.read_jsonl(paths.events_file()) if e.get("kind") == kind]

    def test_stray_then_reconcile(self):
        handle = self.fake_t.launch("claude", "haiku", None, self.work, "hi", "t1")
        self.fake_t.set_idle(handle, True)
        from foreman import sessions
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-stray")
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:00:00Z")

        # Pass 1: busy, idle on 1 pass with no hook event -> NOT yet reconciled
        with mock.patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 0)

        # Pass 2: busy, idle on 2 consecutive passes with no hook event -> reconciled to idle
        with mock.patch("foreman.wake.wake") as mock_wake:
            watch.run_pass()
            self.assertEqual(mock_wake.call_count, 0)  # no wake!
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")
        events = self._events_of_kind("busy_reconciled")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sid"], s["sid"])
        self.assertEqual(events[0]["agent"], "claude")

    def test_really_busy_stays_busy(self):
        handle = self.fake_t.launch("claude", "haiku", None, self.work, "hi", "t-busy")
        self.fake_t.set_idle(handle, False)  # really busy
        from foreman import sessions
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-really-busy")
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:00:00Z")

        watch.run_pass()
        watch.run_pass()
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 0)

    def test_list_failure_no_change(self):
        handle = self.fake_t.launch("claude", "haiku", None, self.work, "hi", "t-fail")
        self.fake_t.set_idle(handle, True)
        from foreman import sessions
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-fail")
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:00:00Z")

        # Pass 1: healthy list, pass count = 1
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")

        # Pass 2: list fails (exception)
        with mock.patch("foreman.transports.fake.FakeTransport.list", side_effect=RuntimeError("transport unavailable")):
            watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 0)

        # Pass 3: healthy list again -> 2nd successful pass, reconciles!
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 1)

    def test_empty_list_no_change(self):
        handle = self.fake_t.launch("claude", "haiku", None, self.work, "hi", "t-empty")
        self.fake_t.set_idle(handle, True)
        from foreman import sessions
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-empty")
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:00:00Z")

        # Pass 1: pass 1
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")

        # Pass 2: list returns empty []
        with mock.patch("foreman.transports.fake.FakeTransport.list", return_value=[]):
            watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 0)

        # Pass 3: healthy list -> reconciles
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 1)

    def test_untrusted_transport_stays_busy(self):
        from foreman import sessions
        from foreman.transports.base import TermInfo
        # agy on orca cannot reliably tell idle from waiting for permission (D26, D27)
        s = sessions.create_session("demo", "antigravity",
                                    transport={"kind": "orca", "handle": "term_orca_agy"},
                                    state="busy", title="agy-orca")
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:00:00Z")

        # Mock orca transport reporting idle=True
        mock_orca = mock.MagicMock()
        mock_orca.list.return_value = [
            TermInfo(handle="term_orca_agy", agent="antigravity", alive=True, idle=True)
        ]
        with mock.patch("foreman.transports.get_transport", return_value=mock_orca):
            watch.run_pass()
            watch.run_pass()
            watch.run_pass()

        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 0)

    def test_hook_event_resets_pass_count(self):
        handle = self.fake_t.launch("claude", "haiku", None, self.work, "hi", "t-hook")
        self.fake_t.set_idle(handle, True)
        from foreman import sessions
        s = sessions.create_session("demo", "claude", transport={"kind": "fake", "handle": handle},
                                    state="busy", title="s-hook")
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:00:00Z")

        # Pass 1: count = 1
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")

        # Hook event arrives, updating last_event_at
        sessions.update_session(s["sid"], last_event_at="2026-11-06T12:01:00Z")

        # Pass 2: hook event arrived, so count resets to 1 (not reconciled)
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "busy")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 0)

        # Pass 3: no new hook event, count = 2 -> reconciles
        watch.run_pass()
        self.assertEqual(sessions.load_session(s["sid"])["state"], "idle")
        self.assertEqual(len(self._events_of_kind("busy_reconciled")), 1)
