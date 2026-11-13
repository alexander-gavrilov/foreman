"""Shared fixture for the trust tests (spec 5.5): a temp HOME whose state folder IS the default one for that HOME (trust
is written only then), a stub `codex` on PATH, and a git project. The real ~/.gemini and ~/.codex are never touched:
every agent file lives under the temp HOME, and the test refuses to run if HOME is not inside the temp dir."""
import json
import os
import subprocess

from tests.helpers import ForemanTestCase, ROOT

STUB = os.path.join(ROOT, "tests", "stubs", "codex")
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, check=True,
                          env=dict(os.environ, **GIT_ENV), stdin=subprocess.DEVNULL).stdout.strip()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def readb(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


class TrustCase(ForemanTestCase):
    def setUp(self):
        super().setUp()
        tmp = os.path.realpath(__import__("tempfile").gettempdir())
        assert os.path.realpath(self.home).startswith(tmp), "the trust tests need a temporary HOME"
        self.state = os.path.join(self.home, ".local", "state", "foreman")
        os.makedirs(self.state)
        self.foreman_home = self.state
        os.environ["FOREMAN_HOME"] = self.state
        self.write_config({"upstream": {"mode": "draft"}})
        self.setenv("CODEX_HOME", None)
        self.codex_log = os.path.join(self.tmp, "codex.log")
        self.setenv("CODEX_STUB_LOG", self.codex_log)
        self.setenv("CODEX_STUB_MODE", None)
        self.codexbin = os.path.join(self.tmp, "codexbin")
        os.makedirs(self.codexbin)
        os.symlink(STUB, os.path.join(self.codexbin, "codex"))
        os.environ["PATH"] = self.codexbin + os.pathsep + os.environ["PATH"]
        self.agy_dir = os.path.join(self.home, ".gemini", "antigravity-cli")
        self.agy = os.path.join(self.agy_dir, "settings.json")
        self.codex_dir = os.path.join(self.home, ".codex")
        self.cfg = os.path.join(self.codex_dir, "config.toml")
        os.makedirs(self.agy_dir)
        os.makedirs(self.codex_dir)
        self.repo = self.make_dir("app")
        git(self.repo, "init", "-q", "-b", "main")
        write(os.path.join(self.repo, "a.txt"), "a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "init")

    def setenv(self, key, value):
        old = os.environ.get(key)
        self.addCleanup(lambda: os.environ.pop(key, None) if old is None else os.environ.__setitem__(key, old))
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def init(self, agents=("antigravity", "codex"), path=None, **kw):
        """foreman init in-process (the launcher path equals what `open` uses). Returns (changes, notes, failures)."""
        from foreman import initproj
        failures = []
        kw.setdefault("transports", [])
        changes, notes = initproj.init_project(path or self.repo, agents=list(agents), failures=failures, **kw)
        return changes, notes, failures

    @property
    def slug(self):
        from foreman import projects
        return projects.project_for_path(self.repo)["slug"]

    def agy_list(self):
        try:
            return json.loads(read(self.agy)).get("trustedWorkspaces")
        except FileNotFoundError:
            return None

    def record(self):
        from foreman import trust
        return trust.load_record()["entries"]

    def codex_calls(self):
        return read(self.codex_log).count("app-server") if os.path.exists(self.codex_log) else 0
