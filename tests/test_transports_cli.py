"""OrcaTransport and HerdrTransport against stub executables (never the real orca / herdr)."""
import os
import unittest

from tests.helpers import ForemanTestCase
from tests.stubutil import StubMixin


def ok(result):
    return {"ok": True, "result": result}


def err(code, message=""):
    return {"ok": False, "error": {"code": code, "message": message or code}}


class OrcaTransportTest(StubMixin, ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.setup_stubs()
        from foreman import transports
        from foreman.transports import base
        self.base = base
        self.t = transports.get_transport("orca")

    def test_is_a_transport(self):
        from foreman.transports.base import Transport
        from foreman.transports.orca import OrcaTransport
        self.assertIsInstance(self.t, OrcaTransport)
        self.assertIsInstance(self.t, Transport)
        self.assertEqual(self.t.kind, "orca")

    def test_list_maps_terminals_and_idle(self):
        self.set_rules([
            {"match": ["terminal", "list"], "json": ok({"terminals": [
                {"handle": "term_a", "title": "fm-api", "worktreePath": "/w/a", "agentIdentity": "claude",
                 "connected": True, "writable": True, "tabId": "T1", "leafId": "L1"},
                {"handle": "term_b", "title": "x", "worktreePath": "/w/b", "agentIdentity": None,
                 "connected": True, "tabId": "T2", "leafId": "L2"},
                {"handle": "term_dead", "connected": False, "tabId": "T3", "leafId": "L3"}]})},
            {"match": ["worktree", "ps"], "json": ok({"worktrees": [{"agents": [
                {"paneKey": "T1:L1", "state": "done"}, {"paneKey": "T9:L9", "state": "working"}]}]})},
        ])
        terms = self.t.list()
        self.assertEqual([x.handle for x in terms], ["term_a", "term_b"])
        a, b = terms
        self.assertEqual((a.title, a.cwd, a.agent, a.alive, a.idle), ("fm-api", "/w/a", "claude", True, True))
        self.assertIsNone(b.idle)
        self.assertIn(["terminal", "list", "--json"], self.calls("orca"))

    def test_list_survives_a_failing_worktree_ps(self):
        self.set_rules([
            {"match": ["terminal", "list"], "json": ok({"terminals": [
                {"handle": "term_a", "connected": True, "tabId": "T1", "leafId": "L1"}]})},
            {"match": ["worktree", "ps"], "code": 1, "json": err("internal")},
        ])
        self.assertIsNone(self.t.list()[0].idle)

    def test_alive(self):
        self.set_rules([
            {"match": ["terminal", "show", "--terminal", "term_a"], "json": ok({"terminal": {"connected": True}})},
            {"match": ["terminal", "show", "--terminal", "term_x"], "json": ok({"terminal": {"connected": False}})},
            {"match": ["terminal", "show", "--terminal", "term_n"], "code": 1, "json": err("terminal_not_found")},
        ])
        self.assertTrue(self.t.alive("term_a"))
        self.assertFalse(self.t.alive("term_x"))
        self.assertFalse(self.t.alive("term_n"))

    def test_launch_command_and_handle(self):
        self.set_rules([
            {"match": ["agent", "launch"], "json": ok({"launch": {"outcome": {"kind": "terminal",
                                                                             "handle": "term_new"}}})},
            {"match": ["terminal", "rename"], "json": ok({"rename": {"handle": "term_new"}})},
        ])
        h = self.t.launch("claude", "sonnet", "high", "/w/proj", "do it", "fm-api")
        self.assertEqual(h, "term_new")
        launch = self.calls("orca")[0]
        self.assertEqual(launch, ["agent", "launch", "--agent", "claude", "--worktree", "path:/w/proj",
                                  "--model", "sonnet", "--effort", "high", "--surface", "terminal",
                                  "--prompt", "do it", "--json"])
        self.assertIn(["terminal", "rename", "--terminal", "term_new", "--title", "fm-api", "--json"],
                      self.calls("orca"))

    def test_launch_without_model_effort_and_failed_rename_is_not_fatal(self):
        self.set_rules([
            {"match": ["agent", "launch"], "json": ok({"launch": {"outcome": {"handle": "term_n"}}})},
            {"match": ["terminal", "rename"], "code": 1, "json": err("terminal_not_found")},
        ])
        self.assertEqual(self.t.launch("antigravity", None, None, "/w", "p", "t"), "term_n")
        launch = self.calls("orca")[0]
        self.assertNotIn("--model", launch)
        self.assertNotIn("--effort", launch)

    def test_launch_failure_raises(self):
        from foreman.transports._proc import TransportError
        self.set_rules([{"match": ["agent", "launch"], "code": 1, "json": err("bad_agent", "no such agent")}])
        with self.assertRaises(TransportError) as cm:
            self.t.launch("claude", None, None, "/w", "p", "t")
        self.assertIn("bad_agent", str(cm.exception))

    def test_send(self):
        self.set_rules([{"match": ["terminal", "send"], "json": ok({"send": {"accepted": True}})}])
        self.t.send("term_a", "hello\nworld")
        self.assertEqual(self.calls("orca"), [["terminal", "send", "--terminal", "term_a", "--text",
                                               "hello\nworld", "--enter", "--json"]])

    def test_send_to_dead_terminal(self):
        self.set_rules([{"match": ["terminal", "send"], "code": 1,
                         "json": err("terminal_not_writable", "terminal_not_writable Terminal prompt request ID: 1")}])
        with self.assertRaises(self.base.TerminalGone):
            self.t.send("term_dead", "x")

    def test_other_gone_codes_on_other_calls(self):
        self.set_rules([{"match": ["terminal"], "code": 1, "json": err("terminal_not_found")}])
        for call in (lambda: self.t.read("h", 5), lambda: self.t.rename("h", "t"),
                     lambda: self.t.wait_idle("h", 1)):
            with self.assertRaises(self.base.TerminalGone):
                call()

    def test_read(self):
        self.set_rules([{"match": ["terminal", "read"], "json": ok({"terminal": {"tail": ["a", "b", "c"]}})}])
        self.assertEqual(self.t.read("term_a", 3), "a\nb\nc")
        self.assertEqual(self.calls("orca")[0], ["terminal", "read", "--terminal", "term_a", "--limit", "3",
                                                 "--json"])

    def test_wait_idle(self):
        self.set_rules([{"match": ["terminal", "wait", "--terminal", "term_a"], "json": ok({"wait": {}})},
                        {"match": ["terminal", "wait", "--terminal", "term_b"], "code": 1,
                         "json": err("timeout", "timeout")}])
        self.assertTrue(self.t.wait_idle("term_a", 12.5))
        self.assertFalse(self.t.wait_idle("term_b", 1))
        self.assertEqual(self.calls("orca")[0], ["terminal", "wait", "--terminal", "term_a", "--for", "tui-idle",
                                                 "--timeout-ms", "12500", "--json"])

    def test_close_is_idempotent(self):
        self.set_rules([{"match": ["terminal", "close", "--terminal", "term_a"], "json": ok({"close": {}})},
                        {"match": ["terminal", "close", "--terminal", "term_x"], "code": 1,
                         "json": err("terminal_not_found")}])
        self.t.close("term_a")
        self.t.close("term_x")
        self.assertEqual(self.calls("orca")[0], ["terminal", "close", "--terminal", "term_a", "--json"])

    def test_orca_cli_command_env_wins(self):
        os.environ["ORCA_CLI_COMMAND"] = os.path.join(os.path.dirname(__file__), "stubs", "orca")
        os.environ["PATH"] = "/usr/bin:/bin"  # `orca` is no longer reachable through PATH
        self.set_rules([{"match": ["terminal", "list"], "json": ok({"terminals": []})}])
        self.assertEqual(self.t.list(), [])

    def test_missing_binary_is_a_transport_error(self):
        from foreman.transports._proc import TransportError
        os.environ["PATH"] = "/nonexistent"
        with self.assertRaises(TransportError):
            self.t.list()


class HerdrTransportTest(StubMixin, ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.setup_stubs()
        from foreman import transports
        from foreman.transports import base
        self.base = base
        self.t = transports.get_transport("herdr")

    PANES = {"id": "cli:pane:list", "result": {"type": "pane_list", "panes": [
        {"pane_id": "w1:p1", "tab_id": "w1:t1", "terminal_title": "zsh", "label": "fm-api",
         "cwd": "/w/a", "agent": "claude", "agent_status": "idle"},
        {"pane_id": "w1:p2", "tab_id": "w1:t2", "label": "", "terminal_title": "agy tab", "cwd": "/w/b",
         "agent": "agy", "agent_status": "working"},
        {"pane_id": "w1:p3", "tab_id": "w1:t3", "terminal_title": "sh", "cwd": "/w/c", "agent": None,
         "agent_status": "unknown"},
        {"pane_id": "w1:p4", "tab_id": "w1:t4", "terminal_title": "q", "cwd": "/w/d", "agent": "claude",
         "agent_status": "blocked"}]}}

    def test_is_a_transport(self):
        from foreman.transports.base import Transport
        from foreman.transports.herdr import HerdrTransport
        self.assertIsInstance(self.t, HerdrTransport)
        self.assertIsInstance(self.t, Transport)
        self.assertEqual(self.t.kind, "herdr")

    def test_list(self):
        self.set_rules([{"match": ["pane", "list"], "json": self.PANES}])
        terms = {x.handle: x for x in self.t.list()}
        self.assertEqual(sorted(terms), ["w1:p1", "w1:p2", "w1:p3", "w1:p4"])
        self.assertEqual((terms["w1:p1"].title, terms["w1:p1"].cwd, terms["w1:p1"].agent, terms["w1:p1"].idle),
                         ("fm-api", "/w/a", "claude", True))
        self.assertEqual(terms["w1:p2"].agent, "antigravity")  # herdr calls it agy
        self.assertIs(terms["w1:p2"].idle, False)
        self.assertIsNone(terms["w1:p3"].idle)
        self.assertIs(terms["w1:p4"].idle, False)  # blocked = waiting, not idle

    def test_alive(self):
        self.set_rules([{"match": ["pane", "list"], "json": self.PANES}])
        self.assertTrue(self.t.alive("w1:p1"))
        self.assertFalse(self.t.alive("w1:p9"))

    def test_launch_sequence(self):
        self.set_rules([
            {"match": ["tab", "create"], "json": {"id": "x", "result": {"type": "tab_created",
                                                                       "tab": {"tab_id": "w1:t7"},
                                                                       "root_pane": {"pane_id": "w1:p7"}}}},
            {"match": ["agent", "start"], "json": {"result": {"type": "ok"}}},
            {"match": ["agent", "prompt"], "json": {"result": {"type": "ok"}}},
        ])
        h = self.t.launch("antigravity", "gemini-3.8-flash-high", None, "/w/proj", "do it", "fm api")
        self.assertEqual(h, "w1:p7")
        calls = self.calls("herdr")
        self.assertEqual(calls[0], ["tab", "create", "--cwd", "/w/proj", "--label", "fm api", "--no-focus"])
        self.assertEqual(calls[1], ["agent", "start", "fm-api", "--kind", "agy", "--pane", "w1:p7", "--",
                                    "--model", "gemini-3.8-flash-high"])
        self.assertEqual(calls[2], ["agent", "prompt", "w1:p7", "do it"])

    def test_launch_claude_with_effort(self):
        self.set_rules([
            {"match": ["tab", "create"], "json": {"result": {"pane": {"pane_id": "w1:p8"}}}},
            {"match": ["agent", "start"], "json": {"result": {}}},
            {"match": ["agent", "prompt"], "json": {"result": {}}},
        ])
        self.t.launch("claude", "opus", "high", "/w", "p", "t")
        self.assertEqual(self.calls("herdr")[1][-5:], ["--", "--model", "opus", "--effort", "high"])

    def test_launch_start_failure_closes_the_pane(self):
        from foreman.transports._proc import TransportError
        self.set_rules([
            {"match": ["tab", "create"], "json": {"result": {"pane": {"pane_id": "w1:p8"}}}},
            {"match": ["agent", "start"], "code": 1, "json": {"error": {"code": "bad", "message": "boom"}}},
            {"match": ["pane", "close"], "json": {"result": {}}},
        ])
        with self.assertRaises(TransportError):
            self.t.launch("claude", None, None, "/w", "p", "t")
        self.assertIn(["pane", "close", "w1:p8"], self.calls("herdr"))

    def test_send_uses_agent_prompt(self):
        self.set_rules([{"match": ["agent", "prompt"], "json": {"result": {}}}])
        self.t.send("w1:p1", "hi")
        self.assertEqual(self.calls("herdr"), [["agent", "prompt", "w1:p1", "hi"]])

    def test_dead_handles(self):
        self.set_rules([
            {"match": ["agent", "prompt"], "code": 1, "json": {"error": {"code": "pane_not_found",
                                                                         "message": "pane x not found"}}},
            {"match": ["agent", "wait"], "code": 1, "json": {"error": {"code": "agent_not_found",
                                                                       "message": "agent target x not found"}}},
            {"match": ["pane", "read"], "code": 1, "json": {"error": {"code": "pane_not_found"}}},
            {"match": ["pane", "list"], "json": self.PANES},
        ])
        for call in (lambda: self.t.send("x", "t"), lambda: self.t.wait_idle("x", 1),
                     lambda: self.t.read("x", 3), lambda: self.t.rename("x", "t")):
            with self.assertRaises(self.base.TerminalGone):
                call()

    def test_read_plain_and_json(self):
        self.set_rules([{"match": ["pane", "read", "w1:p1"], "stdout": "line1\nline2\n"},
                        {"match": ["pane", "read", "w1:p2"], "json": {"result": {"read": {"text": "j1\nj2"}}}}])
        self.assertEqual(self.t.read("w1:p1", 2), "line1\nline2")
        self.assertEqual(self.t.read("w1:p2", 2), "j1\nj2")
        self.assertEqual(self.calls("herdr")[0], ["pane", "read", "w1:p1", "--lines", "2", "--source",
                                                  "recent-unwrapped"])

    def test_wait_idle(self):
        self.set_rules([{"match": ["agent", "wait", "w1:p1"], "json": {"result": {}}},
                        {"match": ["agent", "wait", "w1:p2"], "code": 1,
                         "json": {"error": {"code": "timeout", "message": "timed out"}}}])
        self.assertTrue(self.t.wait_idle("w1:p1", 3))
        self.assertFalse(self.t.wait_idle("w1:p2", 3))
        self.assertEqual(self.calls("herdr")[0], ["agent", "wait", "w1:p1", "--until", "idle", "--timeout", "3000"])

    def test_close_idempotent(self):
        self.set_rules([{"match": ["pane", "close", "w1:p1"], "json": {"result": {}}},
                        {"match": ["pane", "close", "w1:p9"], "code": 1,
                         "json": {"error": {"code": "pane_not_found"}}}])
        self.t.close("w1:p1")
        self.t.close("w1:p9")

    def test_rename_renames_the_tab_of_the_pane(self):
        self.set_rules([{"match": ["pane", "list"], "json": self.PANES},
                        {"match": ["tab", "rename"], "json": {"result": {}}}])
        self.t.rename("w1:p2", "new title")
        self.assertIn(["tab", "rename", "w1:t2", "new title"], self.calls("herdr"))


if __name__ == "__main__":
    unittest.main()
