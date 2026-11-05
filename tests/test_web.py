"""Tests for foreman web: address filter, auth, state_view, HTTP server, SSE, and CLI."""
import http.cookiejar
import json
from unittest import mock
import os
import secrets
import socket
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from tests.helpers import ForemanTestCase, run_cli


class AddressFilterTest(unittest.TestCase):
    def test_loopback(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertTrue(is_allowed_client_ip("127.0.0.1"))
        self.assertTrue(is_allowed_client_ip("127.0.0.2"))
        self.assertTrue(is_allowed_client_ip("::1"))

    def test_rfc1918_private(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertTrue(is_allowed_client_ip("10.0.0.1"))
        self.assertTrue(is_allowed_client_ip("10.254.0.1"))
        self.assertTrue(is_allowed_client_ip("172.16.0.1"))
        self.assertTrue(is_allowed_client_ip("172.31.255.254"))
        self.assertTrue(is_allowed_client_ip("192.168.0.1"))
        self.assertTrue(is_allowed_client_ip("192.168.1.100"))

    def test_cgnat(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertTrue(is_allowed_client_ip("100.64.0.1"))
        self.assertTrue(is_allowed_client_ip("100.100.50.1"))
        self.assertTrue(is_allowed_client_ip("100.127.255.254"))

    def test_link_local(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertTrue(is_allowed_client_ip("169.254.1.1"))
        self.assertTrue(is_allowed_client_ip("fe80::1"))

    def test_ipv6_ula(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertTrue(is_allowed_client_ip("fc00::1"))
        self.assertTrue(is_allowed_client_ip("fd12:3456:789a::1"))

    def test_ipv4_mapped_ipv6(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertTrue(is_allowed_client_ip("::ffff:127.0.0.1"))
        self.assertTrue(is_allowed_client_ip("::ffff:10.0.0.1"))
        self.assertFalse(is_allowed_client_ip("::ffff:8.8.8.8"))

    def test_public_and_invalid_rejected(self):
        from foreman.web.server import is_allowed_client_ip
        self.assertFalse(is_allowed_client_ip("8.8.8.8"))
        self.assertFalse(is_allowed_client_ip("1.1.1.1"))
        self.assertFalse(is_allowed_client_ip("140.82.121.4"))
        self.assertFalse(is_allowed_client_ip("2001:4860:4860::8888"))
        self.assertFalse(is_allowed_client_ip("invalid-ip"))
        self.assertFalse(is_allowed_client_ip(""))


class WebAuthTest(ForemanTestCase):
    def test_pair_and_exchange_lifecycle(self):
        from foreman.web import auth
        from foreman import paths

        code, url = auth.create_pairing_code(label="test-device")
        self.assertEqual(len(code), 8)
        self.assertIn("code=" + code, url)
        # Alphabet: 32-letter unambiguous (digits 2-9 and letters without 0, 1, i, l, o)
        unambiguous_32 = "23456789abcdefghjkmnpqrstuvwxyz"
        self.assertTrue(all(c in unambiguous_32 for c in code.lower()))

        # secrets.json must exist and have mode 0600
        sec_path = paths.secrets_file()
        self.assertTrue(os.path.exists(sec_path))
        mode = os.stat(sec_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)

        # Exchange pairing code
        ok, token, dev_id = auth.exchange_pairing_code(code, label="phone")
        self.assertTrue(ok)
        self.assertTrue(token)
        self.assertTrue(dev_id.startswith("d-"))

        # Token validates
        self.assertTrue(auth.validate_token(token))
        self.assertFalse(auth.validate_token("bad-token"))

        # One-time use: second exchange fails
        ok2, err, _ = auth.exchange_pairing_code(code)
        self.assertFalse(ok2)

        # List devices
        devs = auth.list_devices()
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["id"], dev_id)
        self.assertEqual(devs[0]["label"], "phone")

        # Revoke device
        self.assertTrue(auth.revoke_device(dev_id))
        self.assertFalse(auth.validate_token(token))
        self.assertEqual(len(auth.list_devices()), 0)


class StateViewTest(ForemanTestCase):
    def test_triage_grouping_cases(self):
        from foreman import projects, sessions
        from foreman.web import state_view

        # Case 1: Live idle orchestrator -> quiet
        p1 = self.init_project("p-quiet")
        sessions.create_session("p-quiet", "claude", role="orchestrator", state="idle")
        st1 = state_view.build_state()
        p1_state = next(p for p in st1["projects"] if p["slug"] == "p-quiet")
        self.assertEqual(p1_state["group"], "quiet")
        self.assertEqual(p1_state["dot"], "grey")

        # Case 2: Live orchestrator + busy session -> working
        p2 = self.init_project("p-working")
        sessions.create_session("p-working", "claude", role="orchestrator", state="idle")
        sessions.create_session("p-working", "claude", role="worker", state="busy")
        st2 = state_view.build_state()
        p2_state = next(p for p in st2["projects"] if p["slug"] == "p-working")
        self.assertEqual(p2_state["group"], "working")
        self.assertEqual(p2_state["dot"], "green")

        # Case 3: Stuck or gone session -> stuck
        p3 = self.init_project("p-stuck")
        sessions.create_session("p-stuck", "claude", role="orchestrator", state="idle")
        sessions.create_session("p-stuck", "claude", role="worker", state="stuck")
        st3 = state_view.build_state()
        p3_state = next(p for p in st3["projects"] if p["slug"] == "p-stuck")
        self.assertEqual(p3_state["group"], "stuck")
        self.assertEqual(p3_state["dot"], "red")

        # Case 4: No live orchestrator -> stuck
        p4 = self.init_project("p-no-orch")
        st4 = state_view.build_state()
        p4_state = next(p for p in st4["projects"] if p["slug"] == "p-no-orch")
        self.assertEqual(p4_state["group"], "stuck")
        self.assertEqual(p4_state["dot"], "red")

        # Case 5: Unanswered question in chat -> needs_you
        p5 = self.init_project("p-needs-you")
        sessions.create_session("p-needs-you", "claude", role="orchestrator", state="idle")
        projects.chat_append("p-needs-you", "orchestrator", "Merge PR #12?", question=True,
                             options=["Да", "Нет", "Ответить…"])
        st5 = state_view.build_state()
        p5_state = next(p for p in st5["projects"] if p["slug"] == "p-needs-you")
        self.assertEqual(p5_state["group"], "needs_you")
        self.assertEqual(p5_state["dot"], "accent")
        self.assertEqual(p5_state["line"], "Merge PR #12?")
        self.assertEqual(p5_state["unanswered"], 1)

    def test_closed_and_rotated_sessions_do_not_count(self):
        from foreman import sessions
        from foreman.web import state_view

        self.init_project("p-closed")
        orch = sessions.create_session("p-closed", "claude", role="orchestrator", state="idle")
        w = sessions.create_session("p-closed", "claude", role="worker", state="gone")
        sessions.set_lifecycle(w["sid"], "closed")
        r = sessions.create_session("p-closed", "claude", role="worker", state="stuck")
        sessions.set_lifecycle(r["sid"], "rotated")
        st = state_view.build_state()
        self.assertEqual(next(p for p in st["projects"] if p["slug"] == "p-closed")["group"], "quiet")

        # a gone session that is still open (terminal vanished, not yet closed) does make the project stuck
        self.init_project("p-gone")
        sessions.create_session("p-gone", "claude", role="orchestrator", state="idle")
        sessions.create_session("p-gone", "claude", role="worker", state="gone")
        st = state_view.build_state()
        self.assertEqual(next(p for p in st["projects"] if p["slug"] == "p-gone")["group"], "stuck")

        # strip: heaviest and the session count come from live sessions only
        sessions.update_session(w["sid"], context_tokens=900000)
        detail = state_view.build_project("p-closed")
        self.assertEqual(detail["strip"]["sessions"], 1)
        sessions.update_session(orch["sid"], context_tokens=100)
        detail = state_view.build_project("p-closed")
        self.assertEqual(detail["strip"]["heaviest"]["context_tokens"], 100)
        self.assertEqual(len(detail["sessions"]), 3)  # the drawer still lists all of them

    def test_strip_of_project_with_only_closed_sessions(self):
        from foreman import sessions
        from foreman.web import state_view

        self.init_project("p-onlyclosed")
        w = sessions.create_session("p-onlyclosed", "claude", role="worker", state="idle")
        sessions.set_lifecycle(w["sid"], "closed")
        detail = state_view.build_project("p-onlyclosed")
        self.assertEqual(detail["strip"]["sessions"], 0)
        self.assertIsNone(detail["strip"]["heaviest"])

    def test_state_shape_matches_web_api(self):
        from foreman import projects, sessions, store, paths
        from foreman.web import state_view

        p = self.init_project("proj1")
        sessions.create_session("proj1", "claude", role="orchestrator", state="idle")
        projects.add_task("proj1", "Build web API", stage="Dev")

        # Add a finding
        f_dir = paths.findings_dir()
        os.makedirs(f_dir, exist_ok=True)
        store.write_json(os.path.join(f_dir, "f-123.json"), {
            "id": "f-123", "ts": "2026-11-05T12:00:00Z", "title": "Test finding", "status": "draft", "url": None
        })

        st = state_view.build_state(version=42)
        self.assertEqual(st["version"], 42)
        self.assertIn("machine", st)
        self.assertIn("limits", st)
        self.assertIn("claude", st["limits"])
        self.assertEqual(st["limits"]["claude"]["windows"][0]["key"], "weekly")
        self.assertIn("thresholds", st)
        self.assertIn("projects", st)
        self.assertEqual(st["findings_count"], 1)

        # Check project detail shape
        p_detail = state_view.build_project("proj1")
        self.assertEqual(p_detail["slug"], "proj1")
        self.assertIn("strip", p_detail)
        self.assertEqual(p_detail["strip"]["stage"], "Dev")
        self.assertIn("chat", p_detail)
        self.assertIn("board", p_detail)
        self.assertIn("sessions", p_detail)
        self.assertEqual(len(p_detail["sessions"]), 1)
        self.assertIn("lifecycle", p_detail["sessions"][0])
        self.assertEqual(p_detail["sessions"][0]["lifecycle"], "active")
        self.assertIn("watches", p_detail)


class ServerHttpTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman.web import auth
        auth.reset_pairing_rate_limits()
        from foreman.web.server import create_server, StateMonitor
        self.monitor = StateMonitor(poll_interval=0.1)
        self.monitor.start()
        self.server = create_server("127.0.0.1", 0, state_monitor=self.monitor)
        self.port = self.server.server_port
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server._shutdown_requested = True
        self.server.shutdown()
        self.server.server_close()
        self.monitor.stop()
        super().tearDown()

    def _http(self, path, method="GET", headers=None, data=None):
        url = self.base_url + path
        hdrs = dict(headers or {})
        body = None
        if data is not None:
            if isinstance(data, (dict, list)):
                body = json.dumps(data).encode("utf-8")
                hdrs.setdefault("Content-Type", "application/json")
            elif isinstance(data, str):
                body = data.encode("utf-8")
            else:
                body = data

        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
                resp_hdrs = dict(resp.headers)
                resp_body = resp.read().decode("utf-8")
                return status, resp_hdrs, resp_body
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8")
            return exc.code, dict(exc.headers), err_body

    def test_static_index_serves_without_auth(self):
        status, hdrs, body = self._http("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", hdrs.get("Content-Type", ""))
        self.assertIn("foreman", body)
        # the real single-file UI shipped in skills/foreman/web/, not the fallback placeholder
        self.assertGreater(len(body), 5000)

    def test_state_limits_use_recorded_readings(self):
        from foreman import limits
        from foreman.web import state_view
        limits.record_reading("claude", {"seven_day": {"used_pct": 61, "resets_at": "2100-01-01T00:00:00Z"},
                                         "five_hour": {"used_pct": 42, "resets_at": "2100-01-01T00:00:00Z"}})
        limits.record_reading("antigravity", {"weekly": {"used_pct": 95, "resets_at": "2100-01-01T00:00:00Z"}})
        lim = state_view.build_state(version=1)["limits"]
        self.assertEqual([(w["key"], w["used_pct"]) for w in lim["claude"]["windows"]],
                         [("weekly", 61), ("five_hour", 42)])
        self.assertEqual(lim["claude"]["level"], "ok")
        self.assertEqual(lim["antigravity"]["level"], "stop")

    def test_api_state_without_token_returns_401(self):
        status, hdrs, body = self._http("/api/state")
        self.assertEqual(status, 401)
        self.assertIn("Bearer", hdrs.get("WWW-Authenticate", ""))
        data = json.loads(body)
        self.assertEqual(data.get("error"), "unauthorized")

    def test_pairing_flow_and_authenticated_requests(self):
        from foreman.web import auth

        # 1. Invalid pairing code fails
        status, hdrs, body = self._http("/pair?code=invalidcode&json=1")
        self.assertEqual(status, 401)

        # 2. Generate valid pairing code
        code, _ = auth.create_pairing_code(label="browser")

        # 3. Exchange code for device token via GET /pair?code=...&json=1
        status, hdrs, body = self._http("/pair?code=%s&json=1" % code)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data.get("ok"))
        set_cookie = hdrs.get("Set-Cookie", "")
        self.assertIn("fm_token=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=Strict", set_cookie)

        # Extract cookie
        cookie_val = set_cookie.split("fm_token=")[1].split(";")[0]

        # 4. Request /api/state with cookie
        status, hdrs, body = self._http("/api/state", headers={"Cookie": "fm_token=" + cookie_val})
        self.assertEqual(status, 200)
        st = json.loads(body)
        self.assertIn("version", st)
        self.assertIn("machine", st)
        self.assertIn("limits", st)
        self.assertIn("projects", st)

        # 5. Request /api/state with Authorization: Bearer
        status, hdrs, body = self._http("/api/state", headers={"Authorization": "Bearer " + cookie_val})
        self.assertEqual(status, 200)

    def test_project_post_messages_and_reply(self):
        from foreman.web import auth
        from foreman import projects, sessions

        code, _ = auth.create_pairing_code()
        _, dev_token, _ = auth.exchange_pairing_code(code)
        auth_hdr = {"Authorization": "Bearer " + dev_token}

        # Initialize project with idle orchestrator
        p = self.init_project("chatproj")
        from foreman.transports.fake import FakeTransport
        handle = FakeTransport().launch("claude", "m", None, p["path"], None, "orch")
        orch = sessions.create_session("chatproj", "claude", role="orchestrator", state="idle",
                                       transport={"kind": "fake", "handle": handle})
        projects.save_project(dict(p, orchestrator=orch["sid"]))

        # Orchestrator asks a question
        q_msg = projects.chat_append("chatproj", "orchestrator", "Merge PR?", question=True,
                                     options=["Да", "Нет", "Ответить…"])

        # User replies via POST /api/projects/chatproj/messages
        status, hdrs, body = self._http(
            "/api/projects/chatproj/messages",
            method="POST",
            headers=auth_hdr,
            data={"text": "Да", "reply_to": q_msg["id"]}
        )
        self.assertEqual(status, 200)
        resp = json.loads(body)
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp.get("delivery"), "delivered")

        # Verify chat in project detail
        status, hdrs, body = self._http("/api/projects/chatproj", headers=auth_hdr)
        self.assertEqual(status, 200)
        p_data = json.loads(body)
        chat = p_data["chat"]
        self.assertEqual(len(chat), 2)
        # First was question, now answered_by points to reply id
        self.assertEqual(chat[0]["id"], q_msg["id"])
        self.assertEqual(chat[0]["answered_by"], resp["id"])
        self.assertEqual(chat[1]["from"], "user")
        self.assertEqual(chat[1]["text"], "Да")

    def test_findings_endpoint(self):
        from foreman.web import auth
        from foreman import paths, store

        code, _ = auth.create_pairing_code()
        _, dev_token, _ = auth.exchange_pairing_code(code)
        auth_hdr = {"Authorization": "Bearer " + dev_token}

        # Add two findings
        f_dir = paths.findings_dir()
        os.makedirs(f_dir, exist_ok=True)
        store.write_json(os.path.join(f_dir, "f-1.json"), {
            "id": "f-1", "ts": "2026-11-05T12:00:00Z", "title": "Finding 1", "status": "sent", "url": None
        })
        store.write_json(os.path.join(f_dir, "f-2.json"), {
            "id": "f-2", "ts": "2026-11-05T12:05:00Z", "title": "Finding 2", "status": "draft", "url": None
        })

        status, hdrs, body = self._http("/api/findings", headers=auth_hdr)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(len(data.get("findings", [])), 2)
        self.assertEqual(data["findings"][0]["id"], "f-2")  # newest first

    def test_sse_events_receives_changed_after_chat_append(self):
        from foreman.web import auth
        from foreman import projects

        code, _ = auth.create_pairing_code()
        _, dev_token, _ = auth.exchange_pairing_code(code)

        p = self.init_project("sseproj")

        # Connect to SSE stream
        url = self.base_url + "/api/events"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + dev_token})
        resp = urllib.request.urlopen(req, timeout=10)

        # First line is initial ping or stream open
        initial_line = resp.readline().decode("utf-8")
        self.assertIn("ping", initial_line)
        resp.readline()  # empty separator line

        # Append chat message to trigger state change
        projects.chat_append("sseproj", "user", "SSE test message")

        # Read next event from stream
        event_line = resp.readline().decode("utf-8")
        self.assertIn("data:", event_line)
        self.assertIn('"type": "changed"', event_line)
        resp.close()

    def test_address_filter_rejects_forbidden_ip(self):
        from foreman.web.server import ForemanRequestHandler
        from io import BytesIO

        class MockSocket:
            def __init__(self, data):
                self._rfile = BytesIO(data)
                self._wfile = BytesIO()

            def makefile(self, mode, *args, **kwargs):
                if "r" in mode:
                    return self._rfile
                return self._wfile

            def sendall(self, data):
                self._wfile.write(data)

            def settimeout(self, value):
                pass

        sock = MockSocket(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        handler = ForemanRequestHandler(sock, ("8.8.8.8", 50000), self.server)
        response_bytes = sock._wfile.getvalue()
        self.assertIn(b"403", response_bytes)
        self.assertIn(b"Forbidden", response_bytes)

    def test_host_header_dns_rebinding_rejected(self):
        # Requests with an invalid Host header must be rejected with 421 or 403
        status, hdrs, body = self._http("/api/state", headers={"Host": "evil.attacker.com"})
        self.assertIn(status, (421, 403))

        status, hdrs, body = self._http("/api/state", headers={"Host": "attacker.site:8765"})
        self.assertIn(status, (421, 403))

        # Valid Host headers (localhost or 127.0.0.1) pass Host check (even if unauth -> 401)
        status, hdrs, body = self._http("/api/state", headers={"Host": "127.0.0.1:%d" % self.port})
        self.assertEqual(status, 401)

        status, hdrs, body = self._http("/api/state", headers={"Host": "localhost:%d" % self.port})
        self.assertEqual(status, 401)

    def test_csrf_origin_header_on_post(self):
        from foreman.web import auth
        from foreman import projects

        code, _ = auth.create_pairing_code()
        _, dev_token, _ = auth.exchange_pairing_code(code)

        p = self.init_project("csrfproj")

        # 1. POST with cross-origin Origin must be rejected with 403
        status, hdrs, body = self._http(
            "/api/projects/csrfproj/messages",
            method="POST",
            headers={
                "Authorization": "Bearer " + dev_token,
                "Origin": "http://evil.com",
            },
            data={"text": "evil message"}
        )
        self.assertEqual(status, 403)

        # 2. POST with cookie auth and NO Origin header must be rejected with 403 (CSRF risk)
        status, hdrs, body = self._http(
            "/api/projects/csrfproj/messages",
            method="POST",
            headers={
                "Cookie": "fm_token=" + dev_token,
                # No Origin header
            },
            data={"text": "cookie only message"}
        )
        self.assertEqual(status, 403)

        # 3. POST with Bearer token and NO Origin header is allowed (API client)
        status, hdrs, body = self._http(
            "/api/projects/csrfproj/messages",
            method="POST",
            headers={
                "Authorization": "Bearer " + dev_token,
                # No Origin header
            },
            data={"text": "bearer token message"}
        )
        self.assertEqual(status, 200)

        # 4. POST with matching UI Origin is allowed (browser UI with cookie)
        status, hdrs, body = self._http(
            "/api/projects/csrfproj/messages",
            method="POST",
            headers={
                "Cookie": "fm_token=" + dev_token,
                "Origin": "http://127.0.0.1:%d" % self.port,
            },
            data={"text": "matching origin message"}
        )
        self.assertEqual(status, 200)

    def test_csrf_origin_must_match_scheme_host_and_port(self):
        from foreman.web import auth
        code, _ = auth.create_pairing_code()
        _, tok, _ = auth.exchange_pairing_code(code)
        self.init_project("csrfport")
        url = "/api/projects/csrfport/messages"
        own = "127.0.0.1:%d" % self.port

        def post(origin):
            return self._http(url, method="POST", data='{"text":"x"}', headers={
                "Cookie": "fm_token=" + tok, "Content-Type": "text/plain", "Origin": origin})[0]

        # same hostname, other port (e.g. an agent's dev server) is a foreign origin
        self.assertEqual(post("http://localhost:3000"), 403)
        self.assertEqual(post("http://127.0.0.1:3000"), 403)
        self.assertEqual(post("http://127.0.0.1"), 403)  # default port 80 != ours
        self.assertEqual(post("https://" + own), 403)    # other scheme
        self.assertEqual(post("null"), 403)
        self.assertEqual(post("http://" + own), 200)
        # the configured base_url origin (e.g. behind a proxy) is accepted
        self.write_config({"web": {"base_url": "https://foreman.example:8443"}})
        self.assertEqual(post("https://foreman.example:8443"), 200)
        self.assertEqual(post("https://foreman.example"), 403)

    def _raw(self, request_bytes, read_timeout=3):
        import socket
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=read_timeout)
        try:
            sock.sendall(request_bytes)
            chunks = []
            while True:
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    return b"".join(chunks) or None
                if not data:
                    break
                chunks.append(data)
            return b"".join(chunks)
        finally:
            sock.close()

    def _authed_post_raw(self, slug, body, content_length):
        from foreman.web import auth
        code, _ = auth.create_pairing_code()
        _, tok, _ = auth.exchange_pairing_code(code)
        head = ("POST /api/projects/%s/messages HTTP/1.0\r\nHost: 127.0.0.1:%d\r\nAuthorization: Bearer %s\r\n"
                "Content-Type: application/json\r\nContent-Length: %s\r\n\r\n" % (slug, self.port, tok, content_length))
        return self._raw(head.encode() + body)

    def test_post_body_validation(self):
        self.init_project("bodyproj")
        for body in (b'[1,2]', b'"text"', b'42', b'null', b'{bad'):
            resp = self._authed_post_raw("bodyproj", body, len(body))
            self.assertIsNotNone(resp, body)
            self.assertIn(b" 400 ", resp.split(b"\r\n", 1)[0], body)
        # a negative, non-numeric or oversized Content-Length is refused without reading
        resp = self._authed_post_raw("bodyproj", b"", "-1")
        self.assertIn(b" 400 ", resp.split(b"\r\n", 1)[0])
        resp = self._authed_post_raw("bodyproj", b"", "abc")
        self.assertIn(b" 400 ", resp.split(b"\r\n", 1)[0])
        resp = self._authed_post_raw("bodyproj", b"", str(64 * 1024 + 1))
        self.assertIn(b" 413 ", resp.split(b"\r\n", 1)[0])
        # the server is still fine afterwards
        body = b'{"text":"ok"}'
        resp = self._authed_post_raw("bodyproj", body, len(body))
        self.assertIn(b" 200 ", resp.split(b"\r\n", 1)[0])

    def test_stalled_client_is_dropped_by_the_socket_timeout(self):
        from foreman.web import server
        self.assertTrue(server.ForemanRequestHandler.timeout)
        with mock.patch.object(server.ForemanRequestHandler, "timeout", 0.4):
            import socket
            import time
            sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            try:
                sock.sendall(b"GET /api/state HTTP/1.0\r\nHost: 127.0.0.1\r\nX-Slow: ")  # headers never finish
                t0 = time.time()
                data = sock.recv(1024)  # the server closes the connection instead of waiting for ever
                self.assertEqual(data, b"")
                self.assertLess(time.time() - t0, 3)
            finally:
                sock.close()

    def test_pairing_rate_limit_429(self):
        # 5 failed attempts per minute per client IP -> 429 on subsequent attempts
        for _ in range(5):
            status, hdrs, body = self._http("/pair?code=badcode99&json=1")
            self.assertEqual(status, 401)

        # 6th failed attempt triggers 429
        status, hdrs, body = self._http("/pair?code=badcode99&json=1")
        self.assertEqual(status, 429)
        self.assertIn("429", str(status))




class WebCliTest(ForemanTestCase):
    def test_pair_command(self):
        code, out, err = run_cli("web", "pair")
        self.assertEqual(code, 0, err)
        self.assertIn("/pair?code=", out)

    def test_devices_and_revoke_command(self):
        from foreman.web import auth

        # Create two devices
        c1, _ = auth.create_pairing_code(label="dev1")
        _, _, id1 = auth.exchange_pairing_code(c1)
        c2, _ = auth.create_pairing_code(label="dev2")
        _, _, id2 = auth.exchange_pairing_code(c2)

        code, out, err = run_cli("web", "devices", "--json")
        self.assertEqual(code, 0, err)
        devs = json.loads(out)
        self.assertEqual(len(devs), 2)

        # Plain list
        code, out, err = run_cli("web", "devices")
        self.assertEqual(code, 0, err)
        self.assertIn(id1, out)
        self.assertIn(id2, out)

        # Revoke id1
        code, out, err = run_cli("web", "revoke", id1)
        self.assertEqual(code, 0, err)

        code, out, err = run_cli("web", "devices", "--json")
        self.assertEqual(code, 0, err)
        devs = json.loads(out)
        self.assertEqual(len(devs), 1)
        self.assertEqual(devs[0]["id"], id2)
