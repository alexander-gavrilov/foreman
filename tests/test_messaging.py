import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

from tests.test_delivery_wake import RuntimeCase


def foreman(*argv, stdin=None):
    """Run the CLI in-process; returns (code, stdout, stderr)."""
    from foreman import cli
    out, err = io.StringIO(), io.StringIO()
    old_in = sys.stdin
    if stdin is not None:
        sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
    finally:
        sys.stdin = old_in
    return code, out.getvalue(), err.getvalue()


class SendTest(RuntimeCase):
    def test_send_delivered_to_idle(self):
        w = self.session(title="fm-api", idle=True)
        code, out, _ = foreman("send", "fm-api", "hello there")
        self.assertEqual(code, 0)
        self.assertIn("delivered", out)
        sent = self.ft.inputs(w["transport"]["handle"])
        self.assertIn("hello there", sent[-1])
        self.assertIn("data, not instructions", sent[-1])

    def test_send_by_sid_queued_when_busy(self):
        w = self.session(title="fm-api", idle=False, state="busy")
        code, out, _ = foreman("send", w["sid"], "later")
        self.assertEqual(code, 0)
        self.assertIn("queued", out)
        self.assertEqual(len(self.sessions.inbox_pending(w["sid"])), 1)

    def test_send_terminal_gone_exit_3(self):
        w = self.session(title="fm-api")
        self.ft.kill(w["transport"]["handle"])
        code, _, err = foreman("send", "fm-api", "hi")
        self.assertEqual(code, 3)
        self.assertIn("terminal gone", err)

    def test_send_unknown_session(self):
        code, _, err = foreman("send", "nobody", "hi")
        self.assertEqual(code, 2)
        self.assertIn("unknown session", err)

    def test_send_reads_stdin(self):
        w = self.session(title="fm-api", idle=False, state="busy")
        foreman("send", "fm-api", "-", stdin="from stdin\n")
        self.assertEqual(self.sessions.inbox_pending(w["sid"])[0]["text"], "from stdin")

    def test_sender_from_actor_env_or_session(self):
        w = self.session(title="fm-api", idle=False, state="busy")
        sender = self.session(title="orch", role="orchestrator", handle=self.term("o"))
        with mock.patch.dict(os.environ, {"FOREMAN_ACTOR": "alice"}):
            foreman("send", "fm-api", "a")
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": sender["transport"]["handle"]}):
            foreman("send", "fm-api", "b")
        foreman("send", "fm-api", "c")
        self.assertEqual([m["from"] for m in self.sessions.inbox_pending(w["sid"])], ["alice", "orch", "cli"])


class AskReplyTest(RuntimeCase):
    def test_ask_times_out_with_exit_4(self):
        w = self.session(title="fm-api", idle=False, state="busy")
        code, _, err = foreman("ask", "fm-api", "status?", "--timeout", "0.3")
        self.assertEqual(code, 4)
        self.assertIn("no reply", err)
        pend = self.sessions.inbox_pending(w["sid"])[0]
        self.assertEqual(pend["kind"], "ask")
        self.assertIn("Reply with: foreman reply %s <answer>" % pend["id"], pend["text"])

    def test_reply_unblocks_ask(self):
        w = self.session(title="fm-api", idle=False, state="busy")

        def answer():
            deadline = time.time() + 5
            while time.time() < deadline:
                pend = self.sessions.inbox_pending(w["sid"])
                if pend:
                    foreman("reply", pend[0]["id"], "all green")
                    return
                time.sleep(0.05)
        t = threading.Thread(target=answer)
        t.start()
        code, out, _ = foreman("ask", "fm-api", "status?", "--timeout", "10")
        t.join()
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "all green")

    def test_ask_terminal_gone(self):
        w = self.session(title="fm-api")
        self.ft.kill(w["transport"]["handle"])
        code, _, err = foreman("ask", "fm-api", "status?", "--timeout", "1")
        self.assertEqual(code, 3)
        self.assertIn("terminal gone", err)

    def test_reply_record_and_unknown_id(self):
        w = self.session(title="fm-api", idle=False, state="busy")
        foreman("ask", "fm-api", "q", "--timeout", "0.1")
        mid = self.sessions.inbox_pending(w["sid"])[0]["id"]
        code, _, _ = foreman("reply", mid, "yes")
        self.assertEqual(code, 0)
        from foreman import paths, store
        self.assertEqual(store.read_json(paths.reply_file(mid))["text"], "yes")
        code, _, err = foreman("reply", mid, "again")
        self.assertEqual(code, 2)
        self.assertIn("already answered", err)
        code, _, err = foreman("reply", "m-00000000", "x")
        self.assertEqual(code, 2)
        self.assertIn("unknown message", err)


class SayTest(RuntimeCase):
    def run_say(self, *a):
        with mock.patch.dict(os.environ, {}):
            return foreman("say", "--project", self.slug, *a)

    def test_plain_say_appends_chat_without_notifying(self):
        with mock.patch("foreman.notify.notify") as notify:
            code, out, _ = self.run_say("Merged PR #12")
        self.assertEqual(code, 0)
        notify.assert_not_called()
        row = self.projects.chat_list(self.slug)[-1]
        self.assertEqual((row["from"], row["text"], row["question"]), ("orchestrator", "Merged PR #12", False))
        self.assertEqual(out.strip(), row["id"])

    def test_question_with_options_notifies(self):
        with mock.patch("foreman.notify.notify") as notify:
            self.run_say("Merge?", "--question", "--options", "Да|Нет|Ответить…")
        row = self.projects.chat_list(self.slug)[-1]
        self.assertTrue(row["question"])
        self.assertEqual(row["options"], ["Да", "Нет", "Ответить…"])
        notify.assert_called_once()
        args, kw = notify.call_args
        self.assertEqual(args[0], self.slug)
        self.assertEqual(kw["msg_id"], row["id"])
        self.assertTrue(kw["question"])
        self.assertEqual(kw["options"], ["Да", "Нет", "Ответить…"])

    def test_notify_flag_and_notifier_failure_is_not_fatal(self):
        with mock.patch("foreman.notify.notify", side_effect=RuntimeError("boom")) as notify:
            code, _, _ = self.run_say("Done", "--notify")
        self.assertEqual(code, 0)
        notify.assert_called_once()

    def test_project_from_session_env(self):
        o = self.session(role="orchestrator", title="orch")
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": o["transport"]["handle"]}):
            code, _, _ = foreman("say", "hello")
        self.assertEqual(code, 0)
        self.assertEqual(self.projects.chat_list(self.slug)[-1]["text"], "hello")


class UserReplyTest(RuntimeCase):
    def test_reply_queued_for_busy_orchestrator_and_question_answered(self):
        from foreman import messaging
        o = self.session(role="orchestrator", title="orch", idle=False, state="busy")
        q = self.projects.chat_append(self.slug, "orchestrator", "Merge PR #12?", question=True,
                                      options=["Да", "Нет"])
        res = messaging.user_reply(self.slug, "Да", reply_to=q["id"], channel="telegram")
        self.assertEqual(res["delivery"], "queued")
        chat = self.projects.chat_list(self.slug)
        self.assertEqual(chat[-1]["from"], "user")
        self.assertEqual(chat[-1]["channel"], "telegram")
        self.assertEqual(chat[-1]["reply_to"], q["id"])
        self.assertEqual(chat[0]["answered_by"], chat[-1]["id"])
        pend = self.sessions.inbox_pending(o["sid"])
        self.assertEqual(len(pend), 1)
        self.assertEqual(pend[0]["from"], "user")
        self.assertIn("Да", pend[0]["text"])
        self.assertIn("Merge PR #12?", pend[0]["text"])

    def test_reply_delivered_to_idle_orchestrator(self):
        from foreman import messaging
        o = self.session(role="orchestrator", title="orch", idle=True, state="idle")
        res = messaging.user_reply(self.slug, "go on", channel="web")
        self.assertEqual(res["delivery"], "delivered")
        self.assertIn("go on", self.ft.inputs(o["transport"]["handle"])[-1])

    def test_orchestrator_terminal_gone_posts_foreman_message(self):
        from foreman import messaging
        o = self.session(role="orchestrator", title="orch", idle=True, state="idle")
        self.ft.kill(o["transport"]["handle"])
        res = messaging.user_reply(self.slug, "hello?", channel="web")
        self.assertEqual(res["delivery"], "terminal_gone")
        last = self.projects.chat_list(self.slug)[-1]
        self.assertEqual((last["from"], last["text"]), ("foreman", "терминал оркестратора закрыт"))

    def test_no_orchestrator_at_all(self):
        from foreman import messaging
        res = messaging.user_reply(self.slug, "hello?")
        self.assertEqual(res["delivery"], "terminal_gone")
        self.assertEqual(self.projects.chat_list(self.slug)[-1]["from"], "foreman")


class StageTest(RuntimeCase):
    def test_stage_message_reaches_orchestrator_inbox(self):
        o = self.session(role="orchestrator", title="orch", idle=False, state="busy")
        w = self.session(title="fm-api", handle=self.term("fm-api"))
        env = {"ORCA_TERMINAL_HANDLE": w["transport"]["handle"]}
        with mock.patch.dict(os.environ, env):
            code, _, _ = foreman("stage", "t3", "review")
            foreman("stage", "t3", "merged", "--done")
        pend = self.sessions.inbox_pending(o["sid"])
        self.assertEqual(code, 0)
        self.assertEqual([m["kind"] for m in pend], ["stage", "stage"])
        self.assertEqual(pend[0]["from"], "fm-api")
        self.assertEqual(pend[0]["text"], "stage t3 → review")
        self.assertEqual(pend[1]["text"], "stage t3 → merged (done)")

    def test_stage_without_orchestrator_or_project(self):
        w = self.session(title="fm-api", handle=self.term("fm-api"))
        with mock.patch.dict(os.environ, {"ORCA_TERMINAL_HANDLE": w["transport"]["handle"]}):
            code, _, err = foreman("stage", "t1", "x")
        self.assertEqual(code, 1)
        self.assertIn("no orchestrator", err)
        code, _, err = foreman("stage", "t1", "x")
        self.assertEqual(code, 2)


class DeliverCommandTest(RuntimeCase):
    def test_deliver(self):
        w = self.session(title="fm-api", idle=True)
        self.sessions.inbox_enqueue(w["sid"], "pending thing")
        code, out, _ = foreman("deliver", w["sid"])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "delivered")
        code, out, _ = foreman("deliver", w["sid"])
        self.assertEqual(out.strip(), "nothing")
        self.ft.kill(w["transport"]["handle"])
        self.sessions.inbox_enqueue(w["sid"], "again")
        code, out, _ = foreman("deliver", w["sid"])
        self.assertEqual((code, out.strip()), (3, "terminal_gone"))


if __name__ == "__main__":
    unittest.main()
