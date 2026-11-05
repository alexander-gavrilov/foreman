"""Shared test helpers. Every test runs against temporary HOME / FOREMAN_HOME /
FOREMAN_FAKE_TRANSPORT directories; the real ones are never touched."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "skills", "foreman", "lib")
BIN = os.path.join(ROOT, "skills", "foreman", "bin", "foreman")

if LIB not in sys.path:
    sys.path.insert(0, LIB)

_ENV_KEYS = ("HOME", "FOREMAN_HOME", "FOREMAN_FAKE_TRANSPORT", "FOREMAN_NOW",
             "FOREMAN_ACTOR", "XDG_STATE_HOME")


class ForemanTestCase(unittest.TestCase):
    """Creates temp HOME/FOREMAN_HOME/FOREMAN_FAKE_TRANSPORT and points os.environ at them."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())  # LIFO: restored after the temp tree is removed
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="foreman-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = os.path.join(self.tmp, "home")
        self.foreman_home = os.path.join(self.tmp, "state")
        self.fake_transport = os.path.join(self.tmp, "fake-transport")
        self.work = os.path.join(self.tmp, "work")
        for d in (self.home, self.foreman_home, self.fake_transport, self.work):
            os.makedirs(d)
        saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        self.addCleanup(self._restore_env, saved)
        os.environ["HOME"] = self.home
        os.environ["FOREMAN_HOME"] = self.foreman_home
        os.environ["FOREMAN_FAKE_TRANSPORT"] = self.fake_transport
        # the notify dispatcher must never pop a real desktop notification from a test (macOS: terminal-notifier)
        import types
        from unittest import mock
        from foreman import macnotify
        patcher = mock.patch.object(macnotify, "subprocess", types.SimpleNamespace(run=mock.MagicMock()))
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ("FOREMAN_NOW", "FOREMAN_ACTOR", "XDG_STATE_HOME"):
            os.environ.pop(k, None)

    @staticmethod
    def _restore_env(saved):
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def make_dir(self, name):
        """Create a folder under the temp work dir and return its real path."""
        path = os.path.join(self.work, name)
        os.makedirs(path, exist_ok=True)
        return path

    def init_project(self, name="demo", **kw):
        """Create a project folder and register it; returns the project dict."""
        from foreman import projects
        return projects.create_project(self.make_dir(name), **kw)

    def write_config(self, data):
        with open(os.path.join(self.foreman_home, "config.json"), "w") as f:
            json.dump(data, f)


def run_cli(*args, stdin=None, env=None, cwd=None):
    """Run bin/foreman as a subprocess; returns (code, stdout, stderr).

    Refuses to run unless FOREMAN_HOME and HOME point inside a temp dir."""
    base = dict(os.environ)
    base.update(env or {})
    tmproot = os.path.realpath(tempfile.gettempdir())
    for key in ("FOREMAN_HOME", "HOME"):
        val = base.get(key)
        if not val or not os.path.realpath(val).startswith(tmproot):
            raise RuntimeError("run_cli needs a temporary %s (got %r)" % (key, val))
    base["PYTHONDONTWRITEBYTECODE"] = "1"
    base["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, BIN] + [str(a) for a in args],
        input=stdin, capture_output=True, text=True, env=base, cwd=cwd, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr
