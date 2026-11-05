"""Claude Code inter-session messaging as a transport (decision D22).

An undocumented interface, isolated in this one module. A running Claude session publishes
~/.claude/sessions/<pid>.json (sessionId, status, messagingSocketPath) and its peer token in
~/.claude/sessions/<pid>.<hash>.key. A message is two JSON lines on the unix socket: an auth line carrying the
token, then the user message. The token is read only to be sent; it is never logged or put in an error.
It can send to a session, not launch, read, rename or close one."""
import glob
import json
import os
import re
import socket
import time

from foreman.transports import _proc
from foreman.transports.base import TermInfo, TerminalGone, Transport

PREFIX = "claude:"
CONNECT_TIMEOUT_S = 5.0
REPLY_WAIT_S = 0.5
POLL_S = 0.25


def _pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


class ClaudeMsgTransport(Transport):
    kind = "claude-msg"

    def __init__(self, sessions_dir=None):
        self._sessions_dir = sessions_dir

    # -- discovery
    def sessions_dir(self):
        return self._sessions_dir or os.path.join(os.path.expanduser("~"), ".claude", "sessions")

    def _entries(self):
        out = []
        for path in sorted(glob.glob(os.path.join(self.sessions_dir(), "*.json"))):
            if not re.match(r"^\d+\.json$", os.path.basename(path)):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("sessionId") and isinstance(data.get("pid"), int):
                data["_alive"] = _pid_alive(data["pid"])
                out.append(data)
        return out

    def _find(self, handle):
        sid = handle[len(PREFIX):] if isinstance(handle, str) and handle.startswith(PREFIX) else handle
        matches = [e for e in self._entries() if e["sessionId"] == sid]
        live = [e for e in matches if e["_alive"]]
        return (live or matches or [None])[-1]

    def _live(self, handle):
        entry = self._find(handle)
        if entry is None or not entry["_alive"]:
            raise TerminalGone(handle)
        return entry

    def _token(self, pid):
        keys = glob.glob(os.path.join(self.sessions_dir(), "%d.*.key" % pid))
        keys.sort(key=lambda p: (os.path.getmtime(p), p), reverse=True)
        for path in keys:
            try:
                with open(path, encoding="utf-8") as fh:
                    raw = fh.read().strip()
            except OSError:
                continue
            token = None
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    token = data.get("peerToken") or data.get("token")
            except ValueError:
                if re.match(r"^[0-9A-Za-z_-]{16,}$", raw):
                    token = raw
            if isinstance(token, str) and token:
                return token
        raise _proc.TransportError("no readable peer key for Claude session pid %d" % pid)

    # -- Transport interface
    def list(self):
        out = []
        for e in self._entries():
            if not e["_alive"]:
                continue
            status = e.get("status")
            out.append(TermInfo(handle=PREFIX + e["sessionId"], title=e.get("name") or "", cwd=e.get("cwd") or "",
                                agent="claude", alive=True,
                                idle=True if status == "idle" else False if status == "busy" else None))
        return out

    def alive(self, handle):
        entry = self._find(handle)
        return bool(entry and entry["_alive"])

    def send(self, handle, text):
        entry = self._live(handle)
        sock_path = entry.get("messagingSocketPath")
        if not sock_path:
            raise _proc.TransportError("Claude session %s publishes no messaging socket" % entry["sessionId"])
        token = self._token(entry["pid"])
        lines = [{"type": "auth", "token": token},
                 {"type": "user", "message": {"role": "user", "content": text}}]
        payload = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines).encode("utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(CONNECT_TIMEOUT_S)
            try:
                sock.connect(sock_path)
            except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError):
                raise TerminalGone(handle)
            except OSError as exc:
                raise _proc.TransportError("cannot connect to the Claude messaging socket (%s)" % exc.strerror)
            try:
                sock.sendall(payload)
                sock.shutdown(socket.SHUT_WR)
            except (BrokenPipeError, ConnectionResetError):
                raise TerminalGone(handle)
            self._check_reply(sock)
        finally:
            sock.close()

    @staticmethod
    def _check_reply(sock):
        """Best effort: an explicit {"type":"error"} reply means the message was not taken."""
        sock.settimeout(REPLY_WAIT_S)
        buf = b""
        try:
            while len(buf) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        except (socket.timeout, OSError):
            pass
        for raw in buf.decode("utf-8", "replace").splitlines():
            try:
                reply = json.loads(raw)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("type") == "error":
                raise _proc.TransportError("Claude messaging socket rejected the message")

    def wait_idle(self, handle, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self._live(handle).get("status") == "idle":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(min(POLL_S, max(0.0, deadline - time.monotonic())))

    def launch(self, agent, model, effort, cwd, prompt, title):
        raise NotImplementedError("claude-msg cannot launch sessions")

    def read(self, handle, lines):
        raise NotImplementedError("claude-msg cannot read a terminal")

    def close(self, handle):
        raise NotImplementedError("claude-msg cannot close sessions")

    def rename(self, handle, title):
        raise NotImplementedError("claude-msg cannot rename sessions")
