import unittest

from tests.helpers import ForemanTestCase  # noqa: F401  (sets sys.path)
from foreman import adapters, guard


class ArgvFlatteningTest(unittest.TestCase):
    def deny(self, argv):
        text = adapters.shell_command({"command": argv})
        self.assertIsNotNone(guard.check_command(text), (argv, text))

    def test_quote_elements_do_not_hide_options(self):
        self.deny(["git", "log", "'", "--output=f", "'"])

    def test_other_shell_forms_fail_closed(self):
        self.deny(["bash", "-l", "script.sh"])
        self.deny(["sh", "-c", "ls", "extra"])

    def test_plain_script_form_still_unwrapped(self):
        self.assertEqual(adapters.shell_command({"command": ["bash", "-lc", "git status"]}), "git status")
        self.assertEqual(adapters.shell_command({"command": ["git", "status"]}), "git status")
