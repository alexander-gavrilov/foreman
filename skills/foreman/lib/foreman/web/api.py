"""HTTP API handlers and routing for foreman web (docs/build/web-api.md)."""
import json
import logging
import os
import queue
import re
import urllib.parse
from http import cookies

from foreman import paths, projects, sessions, store
from foreman.web import auth, state_view

logger = logging.getLogger(__name__)

# Static index.html path: ../../web/index.html relative to this file
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "web")
_INDEX_PATH = os.path.join(_WEB_DIR, "index.html")


def _extract_token(handler):
    """Extract auth token from Authorization header or fm_token cookie."""
    auth_hdr = handler.headers.get("Authorization") or ""
    if auth_hdr.startswith("Bearer "):
        token = auth_hdr[7:].strip()
        if token:
            return token

    cookie_hdr = handler.headers.get("Cookie")
    if cookie_hdr:
        try:
            c = cookies.SimpleCookie()
            c.load(cookie_hdr)
            if "fm_token" in c:
                return c["fm_token"].value
        except Exception:
            pass

    return None


def _is_authenticated(handler):
    token = _extract_token(handler)
    if not token:
        return False
    return auth.validate_token(token)


def _send_json(handler, status, data, extra_headers=None):
    body = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    if extra_headers:
        for k, v in extra_headers.items():
            handler.send_header(k, v)
    handler.end_headers()
    try:
        handler.wfile.write(body)
        handler.wfile.flush()
    except (OSError, BrokenPipeError):
        pass


def _send_unauthorized(handler):
    _send_json(handler, 401, {"error": "unauthorized", "message": "pairing required"},
               extra_headers={"WWW-Authenticate": "Bearer"})


def handle_static(handler):
    """Serve static index.html without auth."""
    if not os.path.exists(_INDEX_PATH):
        content = b"<!DOCTYPE html><html><body>foreman</body></html>\n"
    else:
        try:
            with open(_INDEX_PATH, "rb") as f:
                content = f.read()
        except OSError:
            content = b"<!DOCTYPE html><html><body>foreman</body></html>\n"

    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(content)))
    handler.end_headers()
    try:
        handler.wfile.write(content)
        handler.wfile.flush()
    except (OSError, BrokenPipeError):
        pass


def handle_pair(handler, parsed_url):
    """GET /pair?code=...[&json=1]"""
    query = urllib.parse.parse_qs(parsed_url.query)
    codes = query.get("code")
    is_json = "1" in query.get("json", []) or "application/json" in handler.headers.get("Accept", "")
    client_ip = handler.client_address[0] if handler.client_address else None

    # Rate limiting: at most 5 failed attempts per minute per client IP
    if not auth.check_pairing_rate_limit(client_ip):
        if is_json:
            _send_json(handler, 429, {"ok": False, "error": "too many failed pairing attempts"},
                       extra_headers={"Retry-After": "60"})
        else:
            handler.send_error(429, "Too Many Requests: failed pairing attempts limit reached")
        return

    if not codes or not codes[0]:
        auth.record_failed_pairing(client_ip)
        if is_json:
            _send_json(handler, 400, {"ok": False, "error": "code query parameter required"})
        else:
            handler.send_response(400)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(b"code query parameter required\n")
        return

    code = codes[0].strip()
    label = query.get("label", [None])[0] or handler.headers.get("User-Agent")
    ok, token_or_err, dev_id = auth.exchange_pairing_code(code, label=label)

    if not ok:
        auth.record_failed_pairing(client_ip)
        if is_json:
            _send_json(handler, 401, {"ok": False, "error": token_or_err})
        else:
            handler.send_response(401)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.end_headers()
            handler.wfile.write(token_or_err.encode("utf-8") + b"\n")
        return

    # Set cookie: fm_token; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict
    cookie_str = "fm_token=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % token_or_err

    if is_json:
        _send_json(handler, 200, {"ok": True, "device_id": dev_id},
                   extra_headers={"Set-Cookie": cookie_str})
    else:
        handler.send_response(303)
        handler.send_header("Location", "/")
        handler.send_header("Set-Cookie", cookie_str)
        handler.end_headers()


def handle_state(handler, state_monitor):
    version = state_monitor.version if state_monitor else 1
    state = state_view.build_state(version=version)
    _send_json(handler, 200, state)


def handle_project_detail(handler, slug):
    p_detail = state_view.build_project(slug)
    if p_detail is None:
        _send_json(handler, 404, {"error": "project not found: %s" % slug})
        return
    _send_json(handler, 200, p_detail)


def handle_post_message(handler, slug):
    try:
        content_length = int(handler.headers.get("Content-Length", 0))
        body = handler.rfile.read(content_length)
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:
        _send_json(handler, 400, {"ok": False, "error": "invalid json: %s" % exc})
        return

    text = payload.get("text")
    option = payload.get("option")
    reply_to = payload.get("reply_to")

    if not text and option:
        text = option

    if not text or not isinstance(text, str) or not text.strip():
        _send_json(handler, 400, {"ok": False, "error": "text is required"})
        return

    text = text.strip()

    # Try messaging.user_reply (owned by WP5)
    try:
        from foreman import messaging
        if hasattr(messaging, "user_reply"):
            res = messaging.user_reply(slug, text, reply_to=reply_to, channel="web")
            if isinstance(res, dict):
                _send_json(handler, 200, res)
                return
    except (ImportError, Exception):
        pass

    # Fallback to projects.chat_append + sessions.inbox_enqueue
    proj = projects.load_project(slug)
    if not proj:
        _send_json(handler, 404, {"ok": False, "error": "project not found: %s" % slug})
        return

    msg = projects.chat_append(slug, sender="user", text=text, channel="web", reply_to=reply_to)
    msg_id = msg["id"]

    # If reply_to, mark the question as answered in chat.jsonl
    if reply_to:
        def mark_answered(rows):
            for row in rows:
                if row.get("id") == reply_to:
                    row["answered_by"] = msg_id
            return rows
        store.rewrite_jsonl(paths.chat_file(slug), mark_answered)

    # Deliver to orchestrator
    proj_sessions = sessions.list_sessions(project=slug)
    orch_sid = proj.get("orchestrator")
    orch_session = None
    if orch_sid:
        orch_session = sessions.find(orch_sid, project=slug)
    if not orch_session:
        candidates = [s for s in proj_sessions if s.get("role") == "orchestrator"]
        orch_session = sessions._prefer_live(candidates)

    if orch_session and orch_session.get("state") not in sessions.INACTIVE_STATES:
        sessions.inbox_enqueue(orch_session["sid"], text, sender="user", kind="message")
        delivery = "delivered" if orch_session.get("state") == "idle" else "queued"
    else:
        delivery = "terminal_gone"
        projects.chat_append(slug, sender="foreman", text="orchestrator terminal is gone", channel="system")

    _send_json(handler, 200, {"ok": True, "id": msg_id, "delivery": delivery})


def handle_findings(handler):
    f_dir = paths.findings_dir()
    findings = []
    try:
        names = sorted(os.listdir(f_dir))
        for name in names:
            if name.endswith(".json"):
                data = store.read_json(os.path.join(f_dir, name))
                if isinstance(data, dict) and data.get("id"):
                    findings.append(data)
    except OSError:
        pass

    findings.sort(key=lambda f: f.get("ts", ""), reverse=True)
    _send_json(handler, 200, {"findings": findings})


def handle_events_sse(handler, state_monitor):
    """Server-Sent Events endpoint /api/events.
    data: {"type": "changed", "version": n}
    : ping every 15 s
    """
    if not state_monitor:
        handler.send_error(503, "state monitor not available")
        return

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    q = state_monitor.subscribe()
    try:
        # Initial ping or connection acknowledgment
        handler.wfile.write(b": ping\n\n")
        handler.wfile.flush()

        while not getattr(handler.server, "_shutdown_requested", False):
            try:
                version = q.get(timeout=15.0)
                event_data = 'data: {"type": "changed", "version": %d}\n\n' % version
                handler.wfile.write(event_data.encode("utf-8"))
                handler.wfile.flush()
            except queue.Empty:
                handler.wfile.write(b": ping\n\n")
                handler.wfile.flush()
            except (OSError, BrokenPipeError, ConnectionResetError):
                break
    finally:
        state_monitor.unsubscribe(q)


def dispatch(handler):
    """Main HTTP request router for ForemanRequestHandler."""
    parsed_url = urllib.parse.urlparse(handler.path)
    path = parsed_url.path
    method = handler.command

    # 1. Static unauthenticated routes
    if method == "GET" and path in ("/", "/index.html"):
        handle_static(handler)
        return

    # 2. Pairing route
    if method == "GET" and path == "/pair":
        handle_pair(handler, parsed_url)
        return

    # 3. All other routes require auth
    if not _is_authenticated(handler):
        _send_unauthorized(handler)
        return

    # CSRF check for state-changing requests (POST, etc.)
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = handler.headers.get("Origin")
        auth_hdr = handler.headers.get("Authorization") or ""
        has_bearer = auth_hdr.startswith("Bearer ")

        if origin:
            parsed_origin = urllib.parse.urlsplit(origin)
            origin_host = (parsed_origin.hostname or "").lower()
            from foreman.web.server import is_allowed_host
            if not is_allowed_host(origin_host):
                _send_json(handler, 403, {"error": "forbidden", "message": "cross-origin request forbidden"})
                return
        else:
            # Absent Origin requires a Bearer token (reject cookie-only POST)
            if not has_bearer:
                _send_json(handler, 403, {"error": "forbidden", "message": "origin header required for cookie auth"})
                return

    state_monitor = getattr(handler.server, "state_monitor", None)

    # 4. API routes
    if method == "GET" and path == "/api/state":
        handle_state(handler, state_monitor)
        return

    if method == "GET" and path == "/api/findings":
        handle_findings(handler)
        return

    if method == "GET" and path == "/api/events":
        handle_events_sse(handler, state_monitor)
        return

    # Projects endpoints
    m_proj = re.match(r"^/api/projects/([^/]+)$", path)
    if method == "GET" and m_proj:
        slug = m_proj.group(1)
        handle_project_detail(handler, slug)
        return

    m_msg = re.match(r"^/api/projects/([^/]+)/messages$", path)
    if method == "POST" and m_msg:
        slug = m_msg.group(1)
        handle_post_message(handler, slug)
        return

    # Fallback: 404
    _send_json(handler, 404, {"error": "not found: %s" % path})
