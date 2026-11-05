"""ClaudeMsgTransport against a socketserver-based fake Claude session under a temporary HOME."""
import json
import os
import socketserver
import subprocess
import sys
import threading
import unittest

from tests.helpers import ForemanTestCase

TOKEN = "0123456789abcdef0123456789abcdef"


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            self.server.lines.append(line.decode("utf-8").rstrip("\n"))
            if self.server.reply:
                self.wfile.write((self.server.reply + "\n").encode())
                self.wfile.flush()


class FakeSession(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path):
        self.lines = []
        self.reply = None
        super().__init__(path, Handler)


class ClaudeMsgTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import transports
        from foreman.transports import base
        self.base = base
        self.sdir = os.path.join(self.home, ".claude", "sessions")
        os.makedirs(self.sdir)
        self.sock = os.path.join(self.tmp, "s.sock")
        self.server = FakeSession(self.sock)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.pid = os.getpid()
        self.write_session(self.pid, "sess-1", "idle", self.sock)
        self.write_key(self.pid, "aa11")
        self.t = transports.get_transport("claude-msg")

    def write_session(self, pid, sid, status, sock, name="personal-5e"):
        with open(os.path.join(self.sdir, "%d.json" % pid), "w") as f:
            json.dump({"pid": pid, "sessionId": sid, "cwd": "/w/p", "name": name, "status": status,
                       "messagingSocketPath": sock}, f)

    def write_key(self, pid, tag, token=TOKEN, raw=False):
        with open(os.path.join(self.sdir, "%d.%s.key" % (pid, tag)), "w") as f:
            f.write(token if raw else json.dumps({"peerToken": token, "other": 1}))

    def dead_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def test_is_a_transport(self):
        from foreman.transports.base import Transport
        from foreman.transports.claude_msg import ClaudeMsgTransport
        self.assertIsInstance(self.t, ClaudeMsgTransport)
        self.assertIsInstance(self.t, Transport)
        self.assertEqual(self.t.kind, "claude-msg")

    def test_list_only_live_sessions(self):
        dead = self.dead_pid()
        self.write_session(dead, "sess-dead", "idle", self.sock)
        self.write_session(self.pid + 1000000, "sess-x", "busy", self.sock)  # pid that is not running
        with open(os.path.join(self.sdir, "junk.json"), "w") as f:
            f.write("not json")
        terms = self.t.list()
        self.assertEqual([x.handle for x in terms], ["claude:sess-1"])
        ti = terms[0]
        self.assertEqual((ti.title, ti.cwd, ti.agent, ti.alive, ti.idle), ("personal-5e", "/w/p", "claude", True, True))

    def test_idle_mapping(self):
        self.write_session(self.pid, "sess-1", "busy", self.sock)
        self.assertIs(self.t.list()[0].idle, False)
        self.write_session(self.pid, "sess-1", "shell", self.sock)
        self.assertIsNone(self.t.list()[0].idle)

    def test_alive(self):
        self.assertTrue(self.t.alive("claude:sess-1"))
        self.assertFalse(self.t.alive("claude:nope"))
        dead = self.dead_pid()
        self.write_session(dead, "sess-dead", "idle", self.sock)
        self.assertFalse(self.t.alive("claude:sess-dead"))

    def test_send_protocol(self):
        self.t.send("claude:sess-1", "hello\nthere")
        import time
        deadline = time.time() + 3
        while len(self.server.lines) < 2 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual([json.loads(x) for x in self.server.lines],
                         [{"type": "auth", "token": TOKEN},
                          {"type": "user", "message": {"role": "user", "content": "hello\nthere"}}])

    def test_token_may_be_a_plain_file_and_newest_key_wins(self):
        self.write_key(self.pid, "aa11", token="a" * 32, raw=True)
        self.write_key(self.pid, "bb22", token="b" * 32, raw=True)
        os.utime(os.path.join(self.sdir, "%d.aa11.key" % self.pid), (1, 1))
        self.t.send("claude:sess-1", "x")
        import time
        time.sleep(0.2)
        self.assertEqual(json.loads(self.server.lines[0])["token"], "b" * 32)

    def test_send_to_unknown_or_dead_pid_is_terminal_gone(self):
        with self.assertRaises(self.base.TerminalGone):
            self.t.send("claude:nope", "x")
        dead = self.dead_pid()
        self.write_session(dead, "sess-dead", "idle", self.sock)
        with self.assertRaises(self.base.TerminalGone):
            self.t.send("claude:sess-dead", "x")

    def test_send_refused_socket_is_terminal_gone(self):
        self.write_session(self.pid, "sess-1", "idle", os.path.join(self.tmp, "no-such.sock"))
        with self.assertRaises(self.base.TerminalGone):
            self.t.send("claude:sess-1", "x")

    def test_missing_key_is_an_error_without_leaking(self):
        from foreman.transports._proc import TransportError
        os.remove(os.path.join(self.sdir, "%d.aa11.key" % self.pid))
        with self.assertRaises(TransportError):
            self.t.send("claude:sess-1", "x")

    def test_server_error_reply_raises_and_never_prints_the_token(self):
        from foreman.transports._proc import TransportError
        self.server.reply = json.dumps({"type": "error", "message": "bad token " + TOKEN})
        with self.assertRaises(TransportError) as cm:
            self.t.send("claude:sess-1", "x")
        self.assertNotIn(TOKEN, str(cm.exception))

    def test_wait_idle(self):
        self.write_session(self.pid, "sess-1", "busy", self.sock)
        self.assertFalse(self.t.wait_idle("claude:sess-1", 0.3))
        self.write_session(self.pid, "sess-1", "idle", self.sock)
        self.assertTrue(self.t.wait_idle("claude:sess-1", 0.3))
        with self.assertRaises(self.base.TerminalGone):
            self.t.wait_idle("claude:nope", 0.1)

    def test_unsupported_operations(self):
        with self.assertRaises(NotImplementedError):
            self.t.launch("claude", None, None, "/w", "p", "t")
        with self.assertRaises(NotImplementedError):
            self.t.close("claude:sess-1")
        with self.assertRaises(NotImplementedError):
            self.t.rename("claude:sess-1", "t")
        with self.assertRaises(NotImplementedError):
            self.t.read("claude:sess-1", 3)


if __name__ == "__main__":
    unittest.main()
