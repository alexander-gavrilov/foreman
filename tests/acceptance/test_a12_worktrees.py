"""Spec §8.12 — two workers opened at once in a git project each work in their own worktree and branch, and neither
changes the other's files. Closing one removes its worktree unless that would lose work, and leaves the other
untouched. Through the real entry point, with the fake transport."""
import os
import subprocess
import unittest

from tests.acceptance.common import AcceptanceCase

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, check=True,
                          env=dict(os.environ, **GIT_ENV), stdin=subprocess.DEVNULL).stdout.strip()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class WorktreesTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slug, self.path = self.make_project()
        git(self.path, "init", "-q", "-b", "main")
        write(os.path.join(self.path, "app.txt"), "v1\n")
        git(self.path, "add", "app.txt")
        git(self.path, "commit", "-q", "-m", "init")

    def test_two_workers_close_one_other_untouched(self):
        a = self.open_worker(self.slug, "fm-api")
        b = self.open_worker(self.slug, "fm-web")
        self.assertNotEqual(a["cwd"], b["cwd"])
        self.assertNotEqual(a["worktree"]["branch"], b["worktree"]["branch"])
        for rec in (a, b):
            self.assertNotEqual(rec["cwd"], self.path)
            self.assertEqual(git(rec["cwd"], "rev-parse", "--abbrev-ref", "HEAD"), rec["worktree"]["branch"])

        # both work at the same time, on the same file
        write(os.path.join(a["cwd"], "app.txt"), "from api\n")
        write(os.path.join(b["cwd"], "app.txt"), "from web\n")
        write(os.path.join(b["cwd"], "web.txt"), "new file\n")
        git(b["cwd"], "add", "-A")
        git(b["cwd"], "commit", "-q", "-m", "web work")
        head_b = git(b["cwd"], "rev-parse", "HEAD")
        with open(os.path.join(a["cwd"], "app.txt")) as f:
            self.assertEqual(f.read(), "from api\n")  # B's write did not reach A
        with open(os.path.join(self.path, "app.txt")) as f:
            self.assertEqual(f.read(), "v1\n")  # nor the main checkout

        # closing A: it has uncommitted work, so its worktree is kept, and B is not touched
        self.fm_ok("close", "--project", self.slug, "fm-api")
        self.assertTrue(os.path.isdir(a["cwd"]))
        self.assertTrue(os.path.isdir(b["cwd"]))
        self.assertEqual(git(b["cwd"], "rev-parse", "HEAD"), head_b)
        self.assertEqual(self.session(b["sid"])["lifecycle"], "active")

        # A's work is thrown away, and a second close takes its worktree back; B still stands
        git(a["cwd"], "checkout", "-q", "--", "app.txt")
        self.fm_ok("close", "--project", self.slug, a["sid"])
        self.assertFalse(os.path.exists(a["cwd"]))
        self.assertNotIn(a["worktree"]["branch"], git(self.path, "branch", "--format=%(refname:short)").split())
        self.assertTrue(os.path.isdir(b["cwd"]))
        with open(os.path.join(b["cwd"], "app.txt")) as f:
            self.assertEqual(f.read(), "from web\n")
        self.assertIn(b["worktree"]["branch"], git(self.path, "branch", "--format=%(refname:short)").split())

        # B has a committed branch: closing it takes the checkout, keeps the unmerged branch
        self.fm_ok("close", "--project", self.slug, "fm-web")
        self.assertFalse(os.path.exists(b["cwd"]))
        self.assertEqual(git(self.path, "rev-parse", b["worktree"]["branch"]), head_b)


if __name__ == "__main__":
    unittest.main()
