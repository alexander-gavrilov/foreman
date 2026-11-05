"""Stub-executable support for CLI-backed transport tests: PATH holds only tests/stubs and the Python
directory, so the real orca / herdr can never be reached."""
import json
import os
import sys
import tempfile
import shutil
from unittest import mock

STUBS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stubs")


class StubMixin:
    def setup_stubs(self):
        self.stub_dir = tempfile.mkdtemp(prefix="foreman-stub-")
        self.addCleanup(shutil.rmtree, self.stub_dir, ignore_errors=True)
        path = os.pathsep.join([STUBS, os.path.dirname(os.path.realpath(sys.executable)),
                                os.path.dirname(sys.executable), "/usr/bin", "/bin"])
        patcher = mock.patch.dict(os.environ, {"PATH": path, "STUB_DIR": self.stub_dir})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("ORCA_CLI_COMMAND", None)
        self.set_rules([])

    def set_rules(self, rules):
        with open(os.path.join(self.stub_dir, "rules.json"), "w") as fh:
            json.dump(rules, fh)

    def calls(self, tool=None):
        try:
            with open(os.path.join(self.stub_dir, "calls.jsonl")) as fh:
                rows = [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []
        return [r["argv"] for r in rows if tool is None or r["tool"] == tool]
