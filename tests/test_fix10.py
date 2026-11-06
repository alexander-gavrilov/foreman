"""Fix round 10: agy transcript reading in context.last_assistant_text (D63)."""
import json
import os
import sys
import unittest
from unittest import mock

from tests.helpers import ForemanTestCase
from tests.test_hooks import HookCase
from foreman import context, messaging, paths, store


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

    def test_a_previous_turns_answer_is_never_returned_as_this_turns(self):
        """The last turn has no text: stop at its USER_INPUT row, do not reach back into the turn before."""
        path = os.path.join(self.tmp, "agy-turns.jsonl")
        rows = [{"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "q1"},
                {"source": "MODEL", "type": "PLANNER_RESPONSE", "content": "answer to q1"},
                {"source": "USER_EXPLICIT", "type": "USER_INPUT", "content": "q2"},
                {"source": "MODEL", "type": "PLANNER_RESPONSE", "content": ""}]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")
        self.assertIsNone(context.last_assistant_text(path))

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


class WebClockTest(unittest.TestCase):
    def test_chat_times_are_the_viewers_local_time_not_the_utc_digits(self):
        """Live: the web chat read 20:40 next to Telegram's 21:40: it cut HH:MM out of the UTC timestamp."""
        web = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "skills", "foreman", "web", "index.html")
        with open(web, encoding="utf-8") as f:
            source = f.read()
        body = source[source.index("function formatClockTime"):]
        body = body[:body.index("function russianPluralSessions")]
        self.assertNotIn(".match(", body)
        self.assertIn("toLocaleTimeString", body)


class NoTestReadsTheRealStdinTest(ForemanTestCase):
    def test_stdin_is_empty_inside_a_test(self):
        self.assertEqual(sys.stdin.read(), "")


if __name__ == "__main__":
    unittest.main()
