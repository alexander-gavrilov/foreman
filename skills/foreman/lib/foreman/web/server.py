"""Foreman web server (architecture §9).

ThreadingHTTPServer bound ONLY to config web.bind addresses and web.port;
rejects (403) clients whose address is not loopback, RFC1918, CGNAT 100.64/10,
link-local or IPv6 ULA/loopback.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import logging
import os
import queue
import socket
import threading
import time

from foreman import config, paths
from foreman.web import api

logger = logging.getLogger(__name__)

CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")


def is_allowed_host(host_hdr):
    """Check if Host header matches configured bind addresses, base_url host, machine name or localhost."""
    if not host_hdr or not isinstance(host_hdr, str):
        return False
    host = host_hdr.strip()
    if host.startswith("[") and "]" in host:
        hostname = host[1:host.index("]")].lower()
    elif ":" in host:
        hostname = host.split(":", 1)[0].lower()
    else:
        hostname = host.lower()

    # Always allowed local hostnames
    if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "::"):
        return True

    cfg = config.load()
    binds = cfg.get("web", {}).get("bind", ["127.0.0.1"])
    if isinstance(binds, str):
        binds = [binds]
    for b in binds:
        b_clean = b.strip().lower()
        if b_clean.startswith("[") and "]" in b_clean:
            b_clean = b_clean[1:b_clean.index("]")]
        if hostname == b_clean:
            return True

    base_url = cfg.get("web", {}).get("base_url")
    if base_url:
        import urllib.parse
        base_host = (urllib.parse.urlsplit(base_url).hostname or "").lower()
        if hostname == base_host:
            return True

    machine_name = (cfg.get("machine", {}).get("name") or "").lower()
    if machine_name and hostname in (machine_name, machine_name + ".local", machine_name + ".lan"):
        return True

    local_host = socket.gethostname().lower()
    if hostname in (local_host, local_host.split(".")[0]):
        return True

    return False


def _origin_tuple(url):
    """(scheme, hostname, port) of an Origin / base URL with the default port filled in; None if malformed."""
    import urllib.parse
    try:
        parts = urllib.parse.urlsplit(url.strip())
        scheme = (parts.scheme or "").lower()
        host = (parts.hostname or "").lower()
        if scheme not in ("http", "https") or not host:
            return None
        port = parts.port or (443 if scheme == "https" else 80)
    except (ValueError, AttributeError):
        return None
    return scheme, host, port


def is_allowed_origin(origin, host_hdr):
    """CSRF check: the Origin must equal scheme://host:port of this server as the browser addressed it (the
    request's Host header, plain http) or of the configured web.base_url. The port is part of the origin:
    SameSite does not separate ports, so a page on another local port must not pass."""
    if not origin or not isinstance(origin, str):
        return False
    got = _origin_tuple(origin)
    if got is None:
        return False
    allowed = []
    if host_hdr and is_allowed_host(host_hdr):
        own = _origin_tuple("http://" + host_hdr.strip())
        if own:
            allowed.append(own)
    base_url = config.get("web.base_url")
    if base_url:
        base = _origin_tuple(base_url)
        if base:
            allowed.append(base)
    return got in allowed


def is_allowed_client_ip(ip_str):
    """Check if client IP is loopback, RFC1918, CGNAT 100.64/10, link-local or IPv6 ULA/loopback."""
    if not ip_str or not isinstance(ip_str, str):
        return False
    try:
        ip = ipaddress.ip_address(ip_str.strip())
    except ValueError:
        return False

    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped

    if ip.is_loopback:
        return True
    if ip.is_private:
        return True
    if ip.is_link_local:
        return True
    if ip in CGNAT_NET:
        return True
    return False


class StateMonitor(threading.Thread):
    """Monitors mtimes of projects/, sessions/, limits/, findings/ every 1s.
    Maintains a version counter and notifies SSE client queues when state changes."""

    def __init__(self, poll_interval=1.0):
        super().__init__(name="foreman-state-monitor", daemon=True)
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self.version = 1
        self._subscribers = set()
        self._lock = threading.Lock()
        self._last_snapshot = self._take_snapshot()

    def _take_snapshot(self):
        """Map of relpath -> mtime_ns for all files under monitored dirs."""
        dirs = [
            paths.projects_dir(),
            paths.sessions_dir(),
            paths.limits_dir(),
            paths.findings_dir(),
        ]
        snap = {}
        for d in dirs:
            if not os.path.exists(d):
                continue
            try:
                for root, _, files in os.walk(d):
                    for name in files:
                        p = os.path.join(root, name)
                        try:
                            snap[p] = os.stat(p).st_mtime_ns
                        except OSError:
                            pass
            except OSError:
                pass
        return snap

    def run(self):
        while not self._stop_event.is_set():
            if self._stop_event.wait(self.poll_interval):
                break
            try:
                current = self._take_snapshot()
                if current != self._last_snapshot:
                    self._last_snapshot = current
                    self.notify_change()
            except Exception as exc:
                logger.debug("StateMonitor error: %s", exc)

    def stop(self):
        self._stop_event.set()

    def notify_change(self):
        with self._lock:
            self.version += 1
            ver = self.version
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(ver)
                except Exception:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)

    def subscribe(self):
        q = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)


class ForemanRequestHandler(BaseHTTPRequestHandler):
    """BaseHTTPRequestHandler with client IP filtering and routing to api.dispatch."""

    def handle_one_request(self):
        """Override to verify client IP after parsing request line and headers."""
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(414)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return

            client_ip = self.client_address[0]
            if not is_allowed_client_ip(client_ip):
                self.send_error(403, "Forbidden: client IP not permitted")
                self.close_connection = True
                return

            host_hdr = self.headers.get("Host", "")
            if not is_allowed_host(host_hdr):
                self.send_error(421, "Misdirected Request: host header not permitted")
                self.close_connection = True
                return

            mname = "do_" + self.command
            if not hasattr(self, mname):
                self.send_error(501, "Unsupported method (%r)" % self.command)
                return
            method = getattr(self, mname)
            method()
            self.wfile.flush()
        except (TimeoutError, OSError):
            self.close_connection = True

    def do_GET(self):
        api.dispatch(self)

    def do_POST(self):
        api.dispatch(self)

    def log_message(self, format, *args):
        # Suppress noisy HTTP logs during testing/normal operation
        logger.debug("%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), format % args))


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def create_server(host, port, state_monitor=None):
    """Create a ThreadingHTTPServer on host:port (supports IPv6 if host contains ':')."""
    server_cls = IPv6ThreadingHTTPServer if ":" in host else ThreadingHTTPServer
    server_cls.allow_reuse_address = True
    server = server_cls((host, port), ForemanRequestHandler)
    server.state_monitor = state_monitor
    server._shutdown_requested = False
    return server


def run_server(binds=None, port=None, stop_event=None, on_ready=None):
    """Run one or more web servers (for each address in binds) until stop_event is set."""
    cfg = config.load()
    if binds is None:
        binds = cfg.get("web", {}).get("bind", ["127.0.0.1"])
    if isinstance(binds, str):
        binds = [binds]
    if port is None:
        port = cfg.get("web", {}).get("port", 8765)

    if stop_event is None:
        stop_event = threading.Event()

    monitor = StateMonitor(poll_interval=1.0)
    monitor.start()

    servers = []
    threads = []
    for host in binds:
        srv = create_server(host, port, state_monitor=monitor)
        servers.append(srv)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        threads.append(t)

    if on_ready:
        on_ready(servers)

    try:
        while not stop_event.is_set():
            if stop_event.wait(0.5):
                break
    finally:
        for srv in servers:
            srv._shutdown_requested = True
            srv.shutdown()
            srv.server_close()
        monitor.stop()
        for t in threads:
            t.join(timeout=1.0)

