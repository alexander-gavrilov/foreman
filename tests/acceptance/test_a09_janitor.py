"""Spec §8.9 — a work folder left for 14 days disappears with no action from the user, after a listing.

The folders are real directories under <state>/agent-work/<project>/, aged with os.utime against the fake clock.
Only the watch loop runs; nobody types `foreman clean`."""
import os
import unittest
from datetime import timedelta

from tests.acceptance.common import AcceptanceCase


class JanitorTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.update_config(notify={"channels": ["file"]})
        self.slug, self.path = self.make_project()

    def work_folder(self, topic, age_days, files=("notes.md", "sub/draft.txt")):
        folder = self.fm_ok("work", "new", topic, "--project", self.slug)
        for rel in files:
            target = os.path.join(folder, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w") as f:
                f.write("scratch\n")
        self.age(folder, age_days)
        return folder

    def age(self, folder, days):
        stamp = (self.clock - timedelta(days=days)).timestamp()
        for root, dirs, files in os.walk(folder, topdown=False):
            for name in files + dirs:
                os.utime(os.path.join(root, name), (stamp, stamp))
        os.utime(folder, (stamp, stamp))

    def notices(self):
        from foreman import paths, store
        return store.read_jsonl(os.path.join(paths.home(), "notify-log.jsonl"))

    def test_an_old_folder_is_announced_then_removed_by_the_watch_loop_alone(self):
        old = self.work_folder("mockups", age_days=15)
        young = self.work_folder("research", age_days=3)

        # first pass: the folder is listed (journal + General-topic notification), still on disk
        self.fm_ok("watch", "run")
        self.assertTrue(os.path.isdir(old))
        self.assertIn(old, " ".join(n["text"] for n in self.notices()))
        journal = self.fm_ok("log", "search", "--project", self.slug, "scheduled for deletion")
        self.assertIn(os.path.basename(old), journal)
        self.assertNotIn(os.path.basename(young), journal)

        # a day later the janitor runs again and removes it: no user action in between
        self.advance(days=1, minutes=1)
        self.fm_ok("watch", "run")
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(young))
        self.assertTrue(os.path.isdir(os.path.dirname(young)))

    def test_a_folder_reaching_the_limit_later_is_announced_one_day_before(self):
        folder = self.work_folder("drafts", age_days=12)
        self.fm_ok("watch", "run")
        self.assertTrue(os.path.isdir(folder))
        self.assertEqual(self.notices(), [])  # 12 days: not yet
        self.advance(days=1, minutes=1)       # 13 days: TTL-1, announced
        self.fm_ok("watch", "run")
        self.assertEqual(len(self.notices()), 1)
        self.assertTrue(os.path.isdir(folder))
        self.advance(days=1, minutes=1)       # 14 days: removed
        self.fm_ok("watch", "run")
        self.assertFalse(os.path.exists(folder))

    def test_a_folder_an_agent_keeps_writing_to_is_not_idle(self):
        folder = self.work_folder("active", age_days=20)
        fresh = os.path.join(folder, "today.txt")
        with open(fresh, "w") as f:
            f.write("still working\n")
        os.utime(fresh, (self.clock.timestamp(), self.clock.timestamp()))
        self.fm_ok("watch", "run")
        self.advance(days=2)
        self.fm_ok("watch", "run")
        self.assertTrue(os.path.isfile(fresh))
        self.assertEqual(self.notices(), [])

    def test_clean_lists_before_it_deletes_and_dry_run_deletes_nothing(self):
        old = self.work_folder("old-notes", age_days=30)
        out = self.fm_ok("clean", "--dry-run")
        self.assertIn(old, out)
        self.assertTrue(os.path.isdir(old))
        out = self.fm_ok("clean")
        self.assertIn(old, out)
        self.assertFalse(os.path.exists(old))

    def test_deletion_never_follows_a_symlink_out_of_the_work_folder(self):
        outside = self.make_dir("precious")
        with open(os.path.join(outside, "keep.txt"), "w") as f:
            f.write("do not delete\n")
        old = self.work_folder("linky", age_days=20)
        os.symlink(outside, os.path.join(old, "link"))
        self.age(old, 20)
        self.fm_ok("watch", "run")
        self.advance(days=1, minutes=1)
        self.fm_ok("watch", "run")
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(os.path.join(outside, "keep.txt")))

    def test_closing_a_task_can_promote_a_summary_instead_of_waiting_14_days(self):
        folder = self.work_folder("spike", age_days=1)
        self.fm_ok("work", "close", folder, "--promote", "spike result: use cursor pagination")
        self.assertFalse(os.path.exists(folder))
        backlog = self.fm_json("board", "show", "--project", self.slug, "--json")["backlog"]
        self.assertEqual(backlog, ["spike result: use cursor pagination"])


if __name__ == "__main__":
    unittest.main()
