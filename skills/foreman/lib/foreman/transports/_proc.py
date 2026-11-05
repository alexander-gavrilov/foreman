"""Subprocess helpers shared by the CLI-backed transports (orca, herdr)."""
import json
import subprocess


class TransportError(RuntimeError):
    """The transport failed in a way that does not mean "the terminal is gone" (binary missing, a CLI
    error, a rejected message). Messages never contain secrets."""


class Result:
    def __init__(self, code, stdout, stderr):
        self.code = code
        self.stdout = stdout or ""
        self.stderr = stderr or ""
        self.data = _parse(self.stdout)
        if self.data is None:
            self.data = _parse(self.stderr)

    @property
    def ok(self):
        if self.code != 0:
            return False
        if isinstance(self.data, dict) and (self.data.get("ok") is False or isinstance(self.data.get("error"), dict)):
            return False
        return True

    def error(self):
        """(code, message) of the failure; code is None when the CLI gave no structured error."""
        if isinstance(self.data, dict) and isinstance(self.data.get("error"), dict):
            err = self.data["error"]
            return str(err.get("code") or ""), str(err.get("message") or "")
        return "", (self.stderr or self.stdout).strip()[:200]

    def result(self):
        """The `result` object of a JSON reply (or the reply itself), {} when there is none."""
        if isinstance(self.data, dict):
            inner = self.data.get("result", self.data)
            return inner if isinstance(inner, dict) else {}
        return {}

    def is_gone(self, tokens):
        code, message = self.error()
        text = (code + " " + message).lower()
        return any(tok in text for tok in tokens)


def _parse(text):
    text = (text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def run(argv, timeout=30):
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise TransportError("%s not found (is it installed and on PATH?)" % argv[0])
    except subprocess.TimeoutExpired:
        raise TransportError("%s timed out after %ss" % (" ".join(argv[:3]), timeout))
    except OSError as exc:
        raise TransportError("cannot run %s: %s" % (argv[0], exc))
    return Result(proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace"))


def failure(result, what):
    code, message = result.error()
    detail = (code + ": " + message) if code and message and code != message else (code or message or "exit %d" % result.code)
    return TransportError("%s failed (%s)" % (what, detail[:200]))


def find_key(obj, key):
    """First value stored under `key` anywhere in a nested JSON structure (breadth first)."""
    queue = [obj]
    while queue:
        node = queue.pop(0)
        if isinstance(node, dict):
            if isinstance(node.get(key), (str, int)) and node.get(key) != "":
                return node[key]
            queue.extend(node.values())
        elif isinstance(node, list):
            queue.extend(node)
    return None
