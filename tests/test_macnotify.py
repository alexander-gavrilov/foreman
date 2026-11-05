"""Unit tests for macnotify (owner: WP6)."""
import unittest
from unittest.mock import patch

from tests.helpers import ForemanTestCase
from foreman import macnotify


class MacNotifyTestCase(ForemanTestCase):
    def test_terminal_notifier_on_path_with_url(self):
        recorded = []

        def fake_runner(cmd):
            recorded.append(cmd)

        with patch("shutil.which", return_value="/usr/local/bin/terminal-notifier"):
            res = macnotify.notify("foreman · demo", "Task done", url="http://127.0.0.1:8765/#/p/demo/m/m-1", runner=fake_runner)

        self.assertTrue(res["ok"])
        self.assertEqual(res["backend"], "terminal-notifier")
        self.assertEqual(len(recorded), 1)
        self.assertEqual(
            recorded[0],
            ["terminal-notifier", "-title", "foreman · demo", "-message", "Task done", "-open", "http://127.0.0.1:8765/#/p/demo/m/m-1"],
        )

    def test_terminal_notifier_on_path_without_url(self):
        recorded = []

        def fake_runner(cmd):
            recorded.append(cmd)

        with patch("shutil.which", return_value="/usr/local/bin/terminal-notifier"):
            res = macnotify.notify("foreman", "Machine alert", url=None, runner=fake_runner)

        self.assertTrue(res["ok"])
        self.assertEqual(res["backend"], "terminal-notifier")
        self.assertEqual(recorded[0], ["terminal-notifier", "-title", "foreman", "-message", "Machine alert"])

    def test_osascript_fallback_with_url(self):
        recorded = []

        def fake_runner(cmd):
            recorded.append(cmd)

        with patch("shutil.which", return_value=None):
            res = macnotify.notify("foreman · demo", "Question asked?", url="http://127.0.0.1:8765/#/p/demo/m/m-2", runner=fake_runner)

        self.assertTrue(res["ok"])
        self.assertEqual(res["backend"], "osascript")
        self.assertEqual(len(recorded), 1)
        cmd = recorded[0]
        self.assertEqual(cmd[0], "osascript")
        self.assertEqual(cmd[1], "-e")
        # Text must include the URL because osascript has no click-through
        self.assertIn("Question asked?", cmd[2])
        self.assertIn("http://127.0.0.1:8765/#/p/demo/m/m-2", cmd[2])
        self.assertIn('with title "foreman · demo"', cmd[2])

    def test_osascript_fallback_escapes_quotes(self):
        recorded = []

        def fake_runner(cmd):
            recorded.append(cmd)

        with patch("shutil.which", return_value=None):
            res = macnotify.notify('title "with quotes"', 'text "with quotes"', runner=fake_runner)

        self.assertTrue(res["ok"])
        cmd = recorded[0]
        self.assertIn(r'\"with quotes\"', cmd[2])

    def test_failure_never_raises(self):
        def failing_runner(cmd):
            raise RuntimeError("command failed")

        res = macnotify.notify("title", "text", runner=failing_runner)
        self.assertFalse(res["ok"])
        self.assertIn("command failed", res.get("error", ""))


if __name__ == "__main__":
    unittest.main()
