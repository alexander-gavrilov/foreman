"""File-backed fake transport for tests, rooted at $FOREMAN_FAKE_TRANSPORT.

Each terminal is <root>/terminals/<handle>.json:
  {handle, title, cwd, agent, model, effort, alive, idle, inputs: [...], output: [...]}
State is shared between instances and processes (tests drive it from the outside with kill/set_idle/
set_output/inputs)."""
import os
import time

from foreman import ids, store
from foreman.transports.base import TermInfo, TerminalGone, Transport


class FakeTransport(Transport):
    kind = "fake"

    def __init__(self, root=None):
        root = root or os.environ.get("FOREMAN_FAKE_TRANSPORT")
        if not root:
            raise RuntimeError("FOREMAN_FAKE_TRANSPORT is not set")
        self.root = root
        self.dir = os.path.join(root, "terminals")

    # -- internals
    def _path(self, handle):
        if not handle or "/" in handle or handle in (".", ".."):
            raise TerminalGone(str(handle))
        return os.path.join(self.dir, handle + ".json")

    def _load(self, handle, require_alive=True):
        rec = store.read_json(self._path(handle))
        if not isinstance(rec, dict) or (require_alive and not rec.get("alive")):
            raise TerminalGone(handle)
        return rec

    def _modify(self, handle, fn, require_alive=True):
        path = self._path(handle)

        def apply(rec):
            if not isinstance(rec, dict) or (require_alive and not rec.get("alive")):
                raise TerminalGone(handle)
            fn(rec)
            return rec
        return store.update_json(path, apply, default=None)

    # -- Transport interface
    def list(self):
        out = []
        try:
            names = sorted(os.listdir(self.dir))
        except OSError:
            return out
        for name in names:
            if not name.endswith(".json"):
                continue
            rec = store.read_json(os.path.join(self.dir, name))
            if isinstance(rec, dict) and rec.get("alive"):
                out.append(TermInfo(handle=rec["handle"], title=rec.get("title", ""), cwd=rec.get("cwd", ""),
                                    agent=rec.get("agent"), alive=True, idle=rec.get("idle")))
        return out

    def alive(self, handle):
        try:
            self._load(handle)
            return True
        except TerminalGone:
            return False

    def launch(self, agent, model, effort, cwd, prompt, title):
        handle = "fake-" + ids.new_msg_id()[2:]
        rec = {"handle": handle, "title": title or handle, "cwd": cwd, "agent": agent, "model": model,
               "effort": effort, "alive": True, "idle": True, "inputs": [prompt] if prompt else [],
               "output": []}
        store.write_json(self._path(handle), rec)
        return handle

    def send(self, handle, text):
        self._modify(handle, lambda rec: rec["inputs"].append(text))

    def read(self, handle, lines):
        rec = self._load(handle)
        return "\n".join(rec.get("output", [])[-lines:] if lines > 0 else [])

    def wait_idle(self, handle, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self._load(handle).get("idle"):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def close(self, handle):
        try:
            self._modify(handle, lambda rec: rec.update(alive=False))
        except TerminalGone:
            pass

    def rename(self, handle, title):
        self._modify(handle, lambda rec: rec.update(title=title))

    # -- test drivers
    def kill(self, handle):
        self._modify(handle, lambda rec: rec.update(alive=False), require_alive=False)

    def set_idle(self, handle, idle=True):
        self._modify(handle, lambda rec: rec.update(idle=bool(idle)))

    def set_output(self, handle, lines):
        self._modify(handle, lambda rec: rec.update(output=list(lines)))

    def inputs(self, handle):
        return list(self._load(handle, require_alive=False).get("inputs", []))
