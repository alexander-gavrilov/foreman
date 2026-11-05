"""Contract changes after review 1/1b: lifecycle, claimed delivery, agent-only terminals, compact."""
import os
import threading
import unittest
from unittest import mock

from tests.test_delivery_wake import RuntimeCase
from tests.test_messaging import foreman


class LifecycleTest(RuntimeCase):
    def test_default_and_setter(self):
        s = self.session()
        self.assertEqual(s["lifecycle"], "active")
        self.sessions.set_lifecycle(s["sid"], "rotated")
        self.assertEqual(self.sessions.load_session(s["sid"])["lifecycle"], "rotated")
        self.assertTrue(self.sessions.is_open(self.sessions.load_session(s["sid"])))
        self.sessions.set_lifecycle(s["sid"], "closed")
        rec = self.sessions.load_session(s["sid"])
        self.assertFalse(self.sessions.is_open(rec))
        self.assertIsNotNone(rec["closed_at"])
        with self.assertRaises(ValueError):
            self.sessions.set_lifecycle(s["sid"], "weird")

    def test_records_without_the_field_count_as_active(self):
        self.assertTrue(self.sessions.is_open({"sid": "s-1", "state": "idle"}))

    def test_wake_ignores_a_closed_orchestrator(self):
        from foreman import wake
        o = self.session(role="orchestrator", title="orch", idle=False, state="busy")
        self.sessions.set_lifecycle(o["sid"], "closed")
        self.assertIsNone(wake.find_orchestrator(self.slug))


class ClaimedDeliveryTest(RuntimeCase):
    def test_a_message_is_never_delivered_twice(self):
        from foreman import delivery
        s = self.session(idle=True)
        self.sessions.inbox_enqueue(s["sid"], "once")
        results = []
        threads = [threading.Thread(target=lambda: results.append(delivery.deliver_pending(s["sid"])))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(results.count("delivered"), 1)
        self.assertEqual(len(self.ft.inputs(s["transport"]["handle"])), 1)

    def test_failed_send_gives_the_messages_back(self):
        from foreman import delivery
        from foreman.transports.base import TerminalGone
        s = self.session(idle=True)
        self.sessions.inbox_enqueue(s["sid"], "keep me")
        with mock.patch("foreman.transports.fake.FakeTransport.send", side_effect=TerminalGone("x")):
            self.assertEqual(delivery.deliver_pending(s["sid"]), "terminal_gone")
        self.assertEqual(len(self.sessions.inbox_pending(s["sid"])), 1)

    def test_other_errors_also_release(self):
        from foreman import delivery
        s = self.session(idle=True)
        self.sessions.inbox_enqueue(s["sid"], "keep me")
        with mock.patch("foreman.transports.fake.FakeTransport.send", side_effect=OSError("pipe")):
            self.assertEqual(delivery.deliver_pending(s["sid"]), "queued")
        self.assertEqual(len(self.sessions.inbox_pending(s["sid"])), 1)


class AgentOnlyTest(RuntimeCase):
    def plain_shell(self, title="sh"):
        h = self.term(title)
        self.ft._modify(h, lambda r: r.update(agent=None))
        return self.session(title=title, handle=h)

    def test_send_ask_compact_refuse_a_plain_shell(self):
        s = self.plain_shell()
        for argv in (("send", "sh", "hi"), ("ask", "sh", "hi"), ("compact", "sh")):
            code, _, err = foreman(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("not an agent terminal", err)
        self.assertEqual(self.ft.inputs(s["transport"]["handle"]), [])
        self.assertEqual(self.sessions.inbox_pending(s["sid"]), [])

    def test_deliver_pending_never_types_into_a_shell(self):
        from foreman import delivery
        s = self.plain_shell()
        self.sessions.inbox_enqueue(s["sid"], "x")
        self.assertEqual(delivery.deliver_pending(s["sid"]), "queued")
        self.assertEqual(self.ft.inputs(s["transport"]["handle"]), [])


class CompactTest(RuntimeCase):
    def test_types_the_raw_command_at_idle(self):
        s = self.session(title="fm-api", idle=True)
        code, out, _ = foreman("compact", "fm-api", "keep", "the", "plan")
        self.assertEqual((code, out.strip()), (0, "delivered"))
        self.assertEqual(self.ft.inputs(s["transport"]["handle"]), ["/compact keep the plan"])
        code, _, _ = foreman("compact", s["sid"])
        self.assertEqual(self.ft.inputs(s["transport"]["handle"])[-1], "/compact")

    def test_busy_session_is_not_interrupted(self):
        s = self.session(title="fm-api", idle=False, state="busy")
        code, _, err = foreman("compact", "fm-api")
        self.assertEqual(code, 1)
        self.assertIn("busy", err)
        self.assertEqual(self.ft.inputs(s["transport"]["handle"]), [])

    def test_antigravity_is_refused_with_a_rotate_hint(self):
        h = self.term("agy")
        s = self.sessions.create_session(self.slug, "antigravity", title="agy", state="idle",
                                         transport={"kind": "fake", "handle": h})
        code, _, err = foreman("compact", "agy")
        self.assertEqual(code, 2)
        self.assertIn("rotate", err)
        self.assertEqual(self.ft.inputs(h), [])

    def test_terminal_gone_and_unknown(self):
        s = self.session(title="fm-api")
        self.ft.kill(s["transport"]["handle"])
        self.assertEqual(foreman("compact", "fm-api")[0], 3)
        self.assertEqual(foreman("compact", "nobody")[0], 2)

    def test_multiline_instructions_are_flattened(self):
        s = self.session(title="fm-api", idle=True)
        foreman("compact", "fm-api", "line one\nline two")
        self.assertEqual(self.ft.inputs(s["transport"]["handle"]), ["/compact line one line two"])


if __name__ == "__main__":
    unittest.main()
