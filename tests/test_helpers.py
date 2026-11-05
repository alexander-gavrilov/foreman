"""The test harness itself (F5, review 3c): nothing in the suite may reach a real GitHub account."""
import os
import subprocess

from tests.helpers import ForemanTestCase, run_cli


class GhGuard(ForemanTestCase):
    def test_upstream_mode_is_draft_in_tests(self):
        from foreman import config
        self.assertEqual(config.load(platform="linux")["upstream"]["mode"], "draft")

    def test_gh_on_path_is_the_failing_shim(self):
        r = subprocess.run(["gh", "issue", "create", "--title", "t"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 97)
        self.assertIn("must not be called", r.stderr)
        self.assertIn("issue create", open(self.gh_shim_log).read())
        self.allow_gh()  # this test called it on purpose

    def test_unguarded_gh_call_would_fail_the_test(self):
        subprocess.run(["gh", "auth", "status"], capture_output=True)
        self.assertTrue(os.path.exists(self.gh_shim_log))
        # what _check_gh_shim does at cleanup, with the failure caught here
        with self.assertRaises(AssertionError):
            self._check_gh_shim()
        self.allow_gh()

    def test_cli_report_defaults_to_a_draft_not_an_issue(self):
        code, out, err = run_cli("report", "something is odd")
        self.assertEqual(code, 0, err)
        self.assertIn("saved as draft", out)
        self.assertFalse(os.path.exists(self.gh_shim_log))
