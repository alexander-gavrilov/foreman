"""Orca transport (architecture §5): every call is `orca <group> <verb> ... --json`.

The binary is $ORCA_CLI_COMMAND (a path or a command line) or `orca` on PATH."""
import os
import shlex

from foreman.transports import _proc
from foreman.transports.base import TermInfo, TerminalGone, Transport

GONE = ("terminal_not_writable", "terminal_not_found", "handle_not_found", "terminal not found")


class OrcaTransport(Transport):
    kind = "orca"

    def _base(self):
        cmd = os.environ.get("ORCA_CLI_COMMAND")
        if cmd:
            return [cmd] if os.path.exists(cmd) else shlex.split(cmd)
        return ["orca"]

    def _call(self, *args, timeout=30):
        return _proc.run(self._base() + list(args) + ["--json"], timeout=timeout)

    def _checked(self, handle, what, *args, timeout=30):
        """Run a call that targets one terminal: TerminalGone for a dead handle, TransportError otherwise."""
        res = self._call(*args, timeout=timeout)
        if res.ok:
            return res
        if res.is_gone(GONE):
            raise TerminalGone(handle)
        raise _proc.failure(res, "orca " + what)

    def _agent_states(self):
        """{paneKey: state} from `orca worktree ps`; empty when unavailable (state is best effort)."""
        try:
            res = self._call("worktree", "ps")
        except _proc.TransportError:
            return {}
        if not res.ok:
            return {}
        states = {}
        for wt in res.result().get("worktrees") or []:
            for agent in (wt.get("agents") or []) if isinstance(wt, dict) else []:
                if isinstance(agent, dict) and agent.get("paneKey"):
                    states[agent["paneKey"]] = agent.get("state")
        return states

    def list(self):
        res = self._call("terminal", "list")
        if not res.ok:
            raise _proc.failure(res, "orca terminal list")
        terminals = [t for t in res.result().get("terminals") or [] if isinstance(t, dict) and t.get("connected")]
        states = self._agent_states() if terminals else {}
        out = []
        for t in terminals:
            state = states.get("%s:%s" % (t.get("tabId"), t.get("leafId")))
            idle = None if state is None else state != "working"
            out.append(TermInfo(handle=t["handle"], title=t.get("title") or "", cwd=t.get("worktreePath") or "",
                                agent=t.get("agentIdentity") or None, alive=True, idle=idle))
        return out

    def alive(self, handle):
        res = self._call("terminal", "show", "--terminal", handle)
        if res.ok:
            inner = res.result()
            term = inner.get("terminal") if isinstance(inner.get("terminal"), dict) else inner
            return bool(term.get("connected"))
        if res.is_gone(GONE):
            return False
        raise _proc.failure(res, "orca terminal show")

    def launch(self, agent, model, effort, cwd, prompt, title):
        args = ["agent", "launch", "--agent", agent, "--worktree", "path:" + cwd]
        if model:
            args += ["--model", model]
        if effort:
            args += ["--effort", effort]
        args += ["--surface", "terminal"]
        if prompt:
            args += ["--prompt", prompt]
        res = self._call(*args, timeout=120)
        if not res.ok:
            raise _proc.failure(res, "orca agent launch")
        launch = res.result().get("launch")
        launch = launch if isinstance(launch, dict) else {}
        outcome = launch.get("outcome") if isinstance(launch.get("outcome"), dict) else {}
        terminal = launch.get("terminal") if isinstance(launch.get("terminal"), dict) else {}
        handle = outcome.get("handle") or launch.get("handle") or terminal.get("handle")
        if not handle:
            raise _proc.TransportError("orca agent launch returned no terminal handle")
        if title:
            try:  # best effort: the agent is already running, losing its handle would orphan it
                self.rename(handle, title)
            except (TerminalGone, _proc.TransportError):
                pass
        return handle

    def send(self, handle, text):
        res = self._checked(handle, "terminal send", "terminal", "send", "--terminal", handle, "--text", text,
                            "--enter")
        sent = res.result().get("send")
        if isinstance(sent, dict) and sent.get("accepted") is False:
            raise _proc.TransportError("orca terminal send: input was not accepted")

    def read(self, handle, lines):
        res = self._checked(handle, "terminal read", "terminal", "read", "--terminal", handle, "--limit",
                            str(int(lines)))
        inner = res.result()
        term = inner.get("terminal") if isinstance(inner.get("terminal"), dict) else inner
        return "\n".join(str(x) for x in term.get("tail") or [])

    def wait_idle(self, handle, timeout):
        res = self._call("terminal", "wait", "--terminal", handle, "--for", "tui-idle", "--timeout-ms",
                         str(int(timeout * 1000)), timeout=timeout + 15)
        if res.ok:
            return True
        if res.is_gone(GONE):
            raise TerminalGone(handle)
        return False  # timeout (exit 1, code "timeout")

    def close(self, handle):
        res = self._call("terminal", "close", "--terminal", handle)
        if not res.ok and not res.is_gone(GONE):
            raise _proc.failure(res, "orca terminal close")

    def rename(self, handle, title):
        self._checked(handle, "terminal rename", "terminal", "rename", "--terminal", handle, "--title", title)
