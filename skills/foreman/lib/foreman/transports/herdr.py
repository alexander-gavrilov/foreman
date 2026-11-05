"""Herdr transport (architecture §5): `herdr <group> <verb> ...` from PATH; replies are JSON."""
import re
import time

from foreman.transports import _proc
from foreman.transports.base import TermInfo, TerminalGone, Transport

GONE = ("pane_not_found", "agent_not_found", "pane not found", "agent target")
KIND_OF_AGENT = {"antigravity": "agy"}          # foreman agent -> `herdr agent start --kind`
AGENT_OF_KIND = {v: k for k, v in KIND_OF_AGENT.items()}
PROMPT_ATTEMPTS = 3
PROMPT_RETRY_DELAY_S = 1.0


class HerdrTransport(Transport):
    kind = "herdr"

    def _call(self, *args, timeout=30):
        return _proc.run(["herdr"] + [str(a) for a in args], timeout=timeout)

    def _checked(self, handle, what, *args, timeout=30):
        res = self._call(*args, timeout=timeout)
        if res.ok:
            return res
        if res.is_gone(GONE):
            raise TerminalGone(handle)
        raise _proc.failure(res, "herdr " + what)

    def _panes(self):
        res = self._call("pane", "list")
        if not res.ok:
            raise _proc.failure(res, "herdr pane list")
        return [p for p in res.result().get("panes") or [] if isinstance(p, dict) and p.get("pane_id")]

    def list(self):
        out = []
        for p in self._panes():
            status = p.get("agent_status")
            idle = True if status in ("idle", "done") else False if status in ("working", "blocked") else None
            agent = p.get("agent") or None
            out.append(TermInfo(handle=p["pane_id"], title=p.get("label") or p.get("terminal_title") or "",
                                cwd=p.get("cwd") or p.get("foreground_cwd") or "",
                                agent=AGENT_OF_KIND.get(agent, agent), alive=True, idle=idle))
        return out

    def alive(self, handle):
        return any(p["pane_id"] == handle for p in self._panes())

    def launch(self, agent, model, effort, cwd, prompt, title):
        args = ["tab", "create", "--cwd", cwd]
        if title:
            args += ["--label", title]
        res = self._call(*(args + ["--no-focus"]))
        if not res.ok:
            raise _proc.failure(res, "herdr tab create")
        pane = _proc.find_key(res.data, "pane_id")
        if not pane:
            raise _proc.TransportError("herdr tab create returned no pane id")
        name = re.sub(r"[^A-Za-z0-9_-]+", "-", title or "").strip("-") or "agent"
        start = ["agent", "start", name, "--kind", KIND_OF_AGENT.get(agent, agent), "--pane", pane]
        extra = (["--model", model] if model else []) + (["--effort", effort] if effort and agent == "claude" else [])
        if extra:
            start += ["--"] + extra
        res = self._call(*start, timeout=120)
        if not res.ok:
            self._call("pane", "close", pane)  # do not leave an empty tab behind
            raise _proc.failure(res, "herdr agent start")
        if prompt:
            self._prompt(pane, prompt)
        return pane

    def _prompt(self, handle, text):
        last = None
        for attempt in range(PROMPT_ATTEMPTS):  # the agent may need a moment before it accepts a prompt
            res = self._call("agent", "prompt", handle, text, timeout=120)
            if res.ok:
                return
            if res.is_gone(GONE):
                raise TerminalGone(handle)
            last = res
            if attempt + 1 < PROMPT_ATTEMPTS:
                time.sleep(PROMPT_RETRY_DELAY_S)
        raise _proc.failure(last, "herdr agent prompt")

    def send(self, handle, text):
        self._checked(handle, "agent prompt", "agent", "prompt", handle, text, timeout=120)

    def read(self, handle, lines):
        res = self._checked(handle, "pane read", "pane", "read", handle, "--lines", int(lines), "--source",
                            "recent-unwrapped")
        if isinstance(res.data, dict):
            read = res.result().get("read")
            if isinstance(read, dict) and "text" in read:
                return str(read["text"])
        return res.stdout.rstrip("\n")

    def wait_idle(self, handle, timeout):
        res = self._call("agent", "wait", handle, "--until", "idle", "--timeout", int(timeout * 1000),
                         timeout=timeout + 15)
        if res.ok:
            return True
        if res.is_gone(GONE):
            raise TerminalGone(handle)
        return False

    def close(self, handle):
        res = self._call("pane", "close", handle)
        if not res.ok and not res.is_gone(GONE):
            raise _proc.failure(res, "herdr pane close")

    def rename(self, handle, title):
        for p in self._panes():
            if p["pane_id"] == handle:
                tab = p.get("tab_id")
                if not tab:
                    raise _proc.TransportError("herdr pane %s has no tab id" % handle)
                self._checked(handle, "tab rename", "tab", "rename", tab, title)
                return
        raise TerminalGone(handle)
