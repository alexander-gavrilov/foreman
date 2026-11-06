"""Shared harness for the spec §8 acceptance scenarios.

Every scenario drives the REAL entry point (`skills/foreman/bin/foreman`, as a subprocess) inside a temporary
HOME / FOREMAN_HOME with the file-backed fake transport. Nothing here can reach the real ~/.claude, ~/.codex,
~/.gemini, ~/.local/state/foreman, api.telegram.org, a real Orca/Herdr terminal, systemd/launchd or GitHub:

  * the subprocess environment is built from scratch (no ORCA_*/HERDR_*/CLAUDE_* variables leak in);
  * PATH holds only a shim directory (fake `claude`, `agy`, `antigravity`, `codex`, `gh`, `orca`, `herdr`,
    `notify-send`, `systemctl`, `launchctl`, ... that LOG their argv and do nothing else) plus the Python dir;
  * "now" is a fake clock (FOREMAN_NOW), advanced by the test;
  * Telegram is the local stub server in tests/fakes/fake_telegram.py;
  * config.json always sets upstream.mode=draft unless a scenario builds an issue with an injected runner.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from tests.helpers import BIN, ForemanTestCase

SHIM_COMMANDS = ("claude", "agy", "antigravity", "codex", "gemini", "gh", "orca", "herdr", "notify-send",
                 "terminal-notifier", "osascript", "systemctl", "launchctl", "brew")
# Commands that would start a model. Scenario 2/4 assert that none of them was ever executed.
MODEL_COMMANDS = ("claude", "agy", "antigravity", "codex", "gemini")

T0 = datetime(2026, 11, 2, 9, 0, 0, tzinfo=timezone.utc)  # Monday 09:00 UTC


def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def read_json(path):
    return json.loads(read_text(path))


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class AcceptanceCase(ForemanTestCase):
    """A machine with a fake clock, a fake transport, command shims and (optionally) a fake Telegram."""

    def setUp(self):
        super().setUp()
        self.clock = T0
        self.shim_dir = os.path.join(self.tmp, "shims")
        self.shim_log = os.path.join(self.tmp, "shim-calls.jsonl")
        os.makedirs(self.shim_dir)
        for name in SHIM_COMMANDS:
            self._make_shim(name)
        self.telegram = None
        self.write_config({"upstream": {"mode": "draft"}, "machine": {"name": "ghome", "kind": "home"}})

    # ------------------------------------------------------------ environment

    def _make_shim(self, name, body=""):
        path = os.path.join(self.shim_dir, name)
        with open(path, "w") as f:
            f.write("#!%s\nimport json, os, sys\n"
                    "with open(%r, 'a') as fh:\n"
                    "    fh.write(json.dumps({'cmd': %r, 'argv': sys.argv[1:]}) + '\\n')\n%s"
                    % (sys.executable, self.shim_log, name, body))
        os.chmod(path, 0o755)

    def shim_calls(self, *names):
        try:
            with open(self.shim_log) as f:
                rows = [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []
        return [r for r in rows if not names or r["cmd"] in names]

    def assert_no_model_started(self):
        self.assertEqual(self.shim_calls(*MODEL_COMMANDS), [], "a model CLI (claude/agy/codex/...) was started")

    def env(self, extra=None):
        python_dir = os.path.dirname(os.path.realpath(sys.executable))
        base = {
            "HOME": self.home,
            "FOREMAN_HOME": self.foreman_home,
            "FOREMAN_FAKE_TRANSPORT": self.fake_transport,
            "FOREMAN_TRANSPORT": "fake",
            "FOREMAN_NOW": iso(self.clock),
            "FOREMAN_OUTBOX_SPAWN": "0",  # no detached Telegram drainer behind the test's back; tests drain explicitly
            "PATH": os.pathsep.join([self.shim_dir, python_dir]),
            "LANG": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        base.update(extra or {})
        return base

    def fm(self, *args, stdin=None, cwd=None, env=None, timeout=60):
        """Run `foreman <args>`; returns (code, stdout, stderr)."""
        tmproot = os.path.realpath(tempfile.gettempdir())
        for key in ("FOREMAN_HOME", "HOME"):
            if not os.path.realpath(self.env()[key]).startswith(tmproot):
                raise RuntimeError("acceptance runs only in a temporary %s" % key)
        proc = subprocess.run([sys.executable, BIN] + [str(a) for a in args], input=stdin, capture_output=True,
                              text=True, env=self.env(env), cwd=cwd or self.work, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr

    def fm_ok(self, *args, **kw):
        code, out, err = self.fm(*args, **kw)
        self.assertEqual(code, 0, "foreman %s -> %s\n%s%s" % (" ".join(map(str, args)), code, out, err))
        return out.strip()

    def fm_json(self, *args, **kw):
        return json.loads(self.fm_ok(*args, **kw))

    # ------------------------------------------------------------ clock

    def advance(self, **delta):
        self.clock = self.clock + timedelta(**delta)
        return self.clock

    # ------------------------------------------------------------ project / sessions

    def make_project(self, name="shopping-list"):
        """`foreman init` on a fresh folder through the CLI; returns (slug, path)."""
        path = self.make_dir(name)
        self.fm_ok("init", "--path", path)
        from foreman import projects
        return projects.project_for_path(path)["slug"], path

    def start_orchestrator(self, slug, agent="claude", title=None):
        """Orchestrator on a fake terminal; returns its session record. Brought to `idle` like a real one
        that has read its briefing (prompt + stop hooks)."""
        args = ["orchestrator", "start", "--project", slug, "--agent", agent, "--transport", "fake", "--json"]
        if title:
            args += ["--title", title]
        rec = self.fm_json(*args)
        self.hook(rec, self.prompt_event(rec))
        self.hook(rec, "Stop")
        return self.session(rec["sid"])

    @staticmethod
    def prompt_event(rec):
        """The native 'the user submitted a prompt' event of the session's agent."""
        return "PreInvocation" if rec["agent"] == "antigravity" else "UserPromptSubmit"

    def open_worker(self, slug, title, agent="claude", model=None, owner="orchestrator", extra=()):
        args = ["open", "--project", slug, "--purpose", "work on %s" % title, "--title", title,
                "--prompt", "start", "--transport", "fake", "--owner", owner, "--json", "--agent", agent]
        if model:
            args += ["--model", model]
        return self.fm_json(*args, *extra)

    def session(self, sid):
        from foreman import sessions
        return sessions.load_session(sid)

    def agent_ids(self):
        return self._agent_ids

    _agent_ids = {}

    def hook(self, rec, event, agent=None, extra=None, cwd=None, env=None, raw=None):
        """Pipe a real hook payload into `foreman hook` for the session `rec`; returns the parsed JSON output."""
        agent = agent or rec["agent"]
        asid = rec.get("agent_session_id") or "agent-" + rec["sid"]
        where = cwd or rec.get("cwd") or self.work
        if agent == "antigravity":  # camelCase dialect; the event name travels as an argument
            payload = {"conversationId": asid, "workspacePaths": [where]}
        else:
            payload = {"hook_event_name": event, "cwd": where, "session_id": asid}
        payload.update(extra or {})
        handle = (rec.get("transport") or {}).get("handle")
        e = {"FOREMAN_TERMINAL_HANDLE": handle} if handle else {}
        e.update(env or {})
        code, out, err = self.fm("hook", "--agent", agent, "--event", event,
                                 stdin=raw if raw is not None else json.dumps(payload), env=e)
        self.assertEqual(code, 0, "hook exited %s: %s" % (code, err))
        return json.loads(out)

    # fake terminals (driven from outside, like the user/agent would)
    def terminal(self):
        from foreman.transports.fake import FakeTransport
        return FakeTransport(self.fake_transport)

    def inputs(self, rec):
        return self.terminal().inputs(rec["transport"]["handle"])

    def wait_for(self, predicate, timeout=15.0, what="condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for %s" % what)

    # ------------------------------------------------------------ fake Telegram

    def start_telegram(self, token="TEST_BOT_TOKEN"):
        from tests.fakes.fake_telegram import FakeTelegramServer
        self.telegram = FakeTelegramServer(token=token).start()
        self.addCleanup(self.telegram.stop)
        self.fm_ok("telegram", "set-token", stdin=token + "\n")
        cfg = self.load_config()
        cfg.setdefault("telegram", {}).update(api_base=self.telegram.base_url, chat_id=self.telegram.chat_id,
                                              allowed_user_ids=[12345])
        cfg["notify"] = {"channels": ["telegram"]}
        cfg["web"] = {"base_url": "http://ghome.lan:8765", "port": 8765, "bind": ["127.0.0.1"]}
        self.write_config(cfg)
        return self.telegram

    def load_config(self):
        with open(os.path.join(self.foreman_home, "config.json")) as f:
            return json.load(f)

    def update_config(self, **sections):
        cfg = self.load_config()
        for key, value in sections.items():
            if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
        self.write_config(cfg)

    # ------------------------------------------------------------ limits

    def claude_statusline(self, weekly=None, five_hour=None, session_id=None, context=None):
        rl = {}
        reset = int((self.clock + timedelta(days=3)).timestamp())
        if weekly is not None:
            rl["seven_day"] = {"used_percentage": weekly, "resets_at": reset}
        if five_hour is not None:
            rl["five_hour"] = {"used_percentage": five_hour, "resets_at": reset}
        payload = {"rate_limits": rl}
        if session_id:
            payload["session_id"] = session_id
        if context is not None:
            payload["context_window"] = {"current_usage": {"input_tokens": context}}
        return payload

    def tap_claude(self, payload):
        """What the user's statusline does on every redraw: `foreman tap statusline --agent claude -- cat`."""
        code, out, err = self.fm("tap", "statusline", "--agent", "claude", "--", "cat", stdin=json.dumps(payload))
        self.assertEqual(code, 0, err)
        return out

    def tap_antigravity(self, payload):
        code, out, err = self.fm("tap", "statusline", "--agent", "antigravity", "--", "cat",
                                 stdin=json.dumps(payload))
        self.assertEqual(code, 0, err)
        return out


def digests(inputs):
    """The foreman digests among the texts typed into a terminal (the first input is the launch prompt)."""
    return [t for t in inputs if t.startswith("[foreman")]


class WebClient:
    """A running `foreman web` (real subprocess on a free port) plus a tiny HTTP client with a cookie jar."""

    def __init__(self, case):
        self.case = case
        self.port = free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.cookie = None
        self.proc = None

    def start(self):
        case = self.case
        case.update_config(web={"port": self.port, "base_url": self.base, "bind": ["127.0.0.1"]})
        self.proc = subprocess.Popen([sys.executable, BIN, "web", "serve", "--port", str(self.port)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=case.env(),
                                     cwd=case.work)
        case.addCleanup(self.stop)
        line = self.proc.stdout.readline()
        case.assertIn("listening on %s" % self.base, line, "web did not start: %s" % line)
        return self

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.proc:
            for stream in (self.proc.stdout, self.proc.stderr):
                stream.close()
            self.proc = None

    def request(self, path, method="GET", body=None, headers=None, cookie=True):
        import urllib.error
        import urllib.request
        hdrs = dict(headers or {})
        if cookie and self.cookie:
            hdrs["Cookie"] = self.cookie
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdrs)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            resp = opener.open(req, timeout=15)
        except urllib.error.HTTPError as err:
            resp = err
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        parsed = json.loads(raw.decode("utf-8")) if "json" in ctype and raw else raw.decode("utf-8", "replace")
        return resp.status, parsed, resp.headers

    def pair(self):
        """`foreman web pair` on the machine, then the browser's GET /pair?code=… (what the user does once)."""
        code = self.case.fm_json("web", "pair", "--json")["code"]
        status, body, headers = self.request("/pair?code=%s&json=1" % code, cookie=False)
        self.case.assertEqual((status, body.get("ok")), (200, True), body)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        return code


import urllib.request  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
