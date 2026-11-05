import json
import os
import shutil
import time
import unittest
from unittest.mock import patch

from tests.helpers import ForemanTestCase, run_cli


class WorkAndCleanTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.init_project("demo")

    def test_work_new(self):
        from foreman import paths

        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:00Z"
        code, out, err = run_cli("work", "new", "test-topic", "--project", "demo")
        self.assertEqual(code, 0, err)

        expected_dir = os.path.join(paths.agent_work_dir("demo"), "2026-11-05-test-topic")
        self.assertEqual(out.strip(), expected_dir)
        self.assertTrue(os.path.isdir(expected_dir))

    def test_work_close_with_promote(self):
        from foreman import paths, projects

        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:00Z"
        code, out, err = run_cli("work", "new", "draft", "--project", "demo")
        self.assertEqual(code, 0, err)
        work_dir = out.strip()
        test_file = os.path.join(work_dir, "notes.txt")
        with open(test_file, "w") as f:
            f.write("some work notes")

        code, out, err = run_cli("work", "close", work_dir, "--promote", "Promoted draft notes")
        self.assertEqual(code, 0, err)

        # Directory deleted
        self.assertFalse(os.path.exists(work_dir))

        # Promoted summary in backlog
        board = projects.load_board("demo")
        self.assertIn("Promoted draft notes", board.get("backlog", []))

    def test_clean_dry_run_and_clean(self):
        from foreman import paths

        # Create two work folders: one recent, one 15 days old
        now_ts = 1793880000  # arbitrary epoch
        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:00Z"

        recent_dir = os.path.join(paths.agent_work_dir("demo"), "2026-11-04-recent")
        old_dir = os.path.join(paths.agent_work_dir("demo"), "2026-10-15-old")
        os.makedirs(recent_dir, exist_ok=True)
        os.makedirs(old_dir, exist_ok=True)

        with open(os.path.join(recent_dir, "file.txt"), "w") as f:
            f.write("recent")
        with open(os.path.join(old_dir, "file.txt"), "w") as f:
            f.write("old")

        from foreman import timeutil
        now_cur = timeutil.now().timestamp()
        # Set mtime of old_dir files to 15 days ago
        fifteen_days_ago = now_cur - (15 * 86400)
        os.utime(os.path.join(old_dir, "file.txt"), (fifteen_days_ago, fifteen_days_ago))
        os.utime(old_dir, (fifteen_days_ago, fifteen_days_ago))

        # clean --dry-run lists old_dir, does not delete
        code, out, err = run_cli("clean", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-15-old", out)
        self.assertTrue(os.path.exists(old_dir))

        # clean deletes old_dir
        code, out, err = run_cli("clean")
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-15-old", out)
        self.assertFalse(os.path.exists(old_dir))
        self.assertTrue(os.path.exists(recent_dir))

    def test_janitor_lifecycle_announce_and_delete(self):
        from foreman import janitor, paths, projects, timeutil

        now_cur = timeutil.now().timestamp()
        folder = os.path.join(paths.agent_work_dir("demo"), "2026-10-23-announcable")
        os.makedirs(folder, exist_ok=True)
        fpath = os.path.join(folder, "draft.txt")
        with open(fpath, "w") as f:
            f.write("hello")

        # 13.5 days idle (between TTL-1=13 and TTL=14)
        idle_13_5 = now_cur - int(13.5 * 86400)
        os.utime(fpath, (idle_13_5, idle_13_5))
        os.utime(folder, (idle_13_5, idle_13_5))

        watch_state = {}
        with patch("foreman.notify.notify") as mock_notify:
            janitor.janitor_pass(watch_state)
            self.assertEqual(mock_notify.call_count, 1)
            # Not deleted yet!
            self.assertTrue(os.path.exists(folder))
            # State tracks announcement
            self.assertIn(folder, watch_state.get("janitor", {}))

            # Journal entry recorded
            journal = projects.journal_tail("demo", 5)
            self.assertTrue(any("scheduled for deletion" in j.get("text", "") for j in journal))

        # Now age reaches 14.5 days (> TTL)
        idle_14_5 = now_cur - int(14.5 * 86400)
        os.utime(fpath, (idle_14_5, idle_14_5))
        os.utime(folder, (idle_14_5, idle_14_5))

        with patch("foreman.notify.notify") as mock_notify:
            janitor.janitor_pass(watch_state)
            # Was announced earlier -> deleted!
            self.assertFalse(os.path.exists(folder))

    def test_janitor_does_not_delete_at_ttl_if_not_announced_earlier(self):
        from foreman import janitor, paths, timeutil

        now_cur = timeutil.now().timestamp()
        folder = os.path.join(paths.agent_work_dir("demo"), "2026-10-15-unannounced")
        os.makedirs(folder, exist_ok=True)
        fpath = os.path.join(folder, "draft.txt")
        with open(fpath, "w") as f:
            f.write("hello")

        # Directly 15 days old without previous announcement
        idle_15 = now_cur - (15 * 86400)
        os.utime(fpath, (idle_15, idle_15))
        os.utime(folder, (idle_15, idle_15))

        watch_state = {}
        with patch("foreman.notify.notify") as mock_notify:
            janitor.janitor_pass(watch_state)
            # Announced now, NOT deleted yet!
            self.assertEqual(mock_notify.call_count, 1)
            self.assertTrue(os.path.exists(folder))
            self.assertIn(folder, watch_state.get("janitor", {}))

    def test_symlink_safety(self):
        from foreman import janitor, paths

        # Outside directory that must NEVER be deleted or modified
        outside_dir = os.path.join(self.tmp, "outside-secret")
        os.makedirs(outside_dir, exist_ok=True)
        secret_file = os.path.join(outside_dir, "secret.txt")
        with open(secret_file, "w") as f:
            f.write("super secret")

        # Work folder containing a symlink to outside_dir
        work_dir = os.path.join(paths.agent_work_dir("demo"), "work-with-symlink")
        os.makedirs(work_dir, exist_ok=True)
        symlink_path = os.path.join(work_dir, "link-to-outside")
        os.symlink(outside_dir, symlink_path)

        # Deleting the work folder
        janitor.safe_delete_dir(work_dir)

        # Work dir is gone
        self.assertFalse(os.path.exists(work_dir))
        # Outside dir and secret file are completely untouched!
        self.assertTrue(os.path.exists(outside_dir))
        self.assertTrue(os.path.exists(secret_file))

        # safe_delete_dir refuses paths outside agent_work_root
        with self.assertRaises(ValueError):
            janitor.safe_delete_dir(outside_dir)

    def test_rotated_sessions_untouched_by_janitor(self):
        from foreman import janitor, sessions

        s = sessions.create_session("demo", "claude", state="idle", lifecycle="rotated", title="s-rot")
        watch_state = {}
        janitor.janitor_pass(watch_state)

        loaded = sessions.load_session(s["sid"])
        self.assertEqual(loaded["lifecycle"], "rotated")
        self.assertEqual(loaded["state"], "idle")

