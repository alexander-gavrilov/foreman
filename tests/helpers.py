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
             "FOREMAN_ACTOR", "XDG_STATE_HOME", "PATH")

GH_SHIM = """#!/bin/sh
# test shim: the real gh must never run from a test (it would use the developer's or CI's credentials)
echo "$0 $*" >> "$FOREMAN_GH_SHIM_LOG"
echo "gh must not be called from a test" >&2
exit 97
"""


def real_default_homes():
    """The state folders foreman would use without FOREMAN_HOME on this machine (computed from the account and the
    environment the test process started with): no test may create or write them."""
    homes = set()
    try:
        import pwd
        homes.add(os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".local", "state", "foreman"))
    except Exception:
        pass
    homes.add(os.path.join(_START_STATE_HOME, "foreman"))
    return sorted(homes)


_START_STATE_HOME = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")


def _home_snapshot(path):
    """None when the folder does not exist; else how many mirror_error rows its events.jsonl holds. A developer's
    own foreman may legitimately write there while the suite runs, so only what a leaked test thread writes counts."""
    if not os.path.isdir(path):
        return None
    try:
        with open(os.path.join(path, "events.jsonl"), "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for line in f if '"mirror_error"' in line)
    except OSError:
        return 0


class ForemanTestCase(unittest.TestCase):
    """Creates temp HOME/FOREMAN_HOME/FOREMAN_FAKE_TRANSPORT and points os.environ at them."""

    def setUp(self):
        super().setUp()
        self.addCleanup(os.chdir, os.getcwd())  # LIFO: restored after the temp tree is removed
        before = {h: _home_snapshot(h) for h in real_default_homes()}
        self.addCleanup(self._assert_default_home_untouched, before)
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
        # Reporting upstream is draft-only in tests, and a `gh` shim first on PATH fails the test if anything
        # runs it. A test that really wants gh calls self.allow_gh() (and provides its own gh on PATH).
        self.write_config({"upstream": {"mode": "draft"}})
        shim_dir = os.path.join(self.tmp, "gh-shim")
        os.makedirs(shim_dir)
        self.gh_shim_log = os.path.join(shim_dir, "calls.log")
        shim = os.path.join(shim_dir, "gh")
        with open(shim, "w") as f:
            f.write(GH_SHIM)
        os.chmod(shim, 0o755)
        os.environ["FOREMAN_GH_SHIM_LOG"] = self.gh_shim_log
        os.environ["PATH"] = shim_dir + os.pathsep + os.environ.get("PATH", "")
        self._gh_allowed = False
        self.addCleanup(self._check_gh_shim)
        self.addCleanup(self._reap_delivery_children)  # LIFO: runs before the temp tree goes
        self.addCleanup(self._flush_mirrors)  # ... and before the environment is restored (mirror threads)

    @staticmethod
    def _flush_mirrors():
        """Telegram mirror threads (messaging._mirror) must finish while the temp environment is still set."""
        from foreman import messaging
        messaging.flush_mirrors(15)

    def _assert_default_home_untouched(self, before):
        changed = [h for h, snap in before.items() if _home_snapshot(h) != snap]
        if changed:
            self.fail("the default foreman home was created or written by a test: " + ", ".join(changed))

    def allow_gh(self):
        """Opt out of the gh guard (the test supplies its own gh on PATH)."""
        self._gh_allowed = True

    @staticmethod
    def _reap_delivery_children():
        """Detached `foreman deliver` children spawned by hooks: wait for them (bounded), so none outlives the
        test as a ResourceWarning."""
        from foreman import delivery
        for child in list(delivery._children):
            try:
                child.wait(timeout=15)
            except Exception:
                child.kill()
                child.wait()
        delivery._children[:] = []

    def _check_gh_shim(self):
        os.environ.pop("FOREMAN_GH_SHIM_LOG", None)
        if not self._gh_allowed and os.path.exists(self.gh_shim_log):
            with open(self.gh_shim_log) as f:
                self.fail("the real gh was called from a test: " + f.read().strip())

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
