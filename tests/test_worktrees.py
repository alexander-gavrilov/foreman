"""Per-worker git worktrees: `foreman open` gives each orchestrator-owned worker its own checkout; `close` takes it
back when clean. Orchestrator and user-owned sessions, and projects that are not git repositories, are unchanged."""
import json
import os
import subprocess
import unittest

from tests.test_cmd_sessions import SessionTestCase, run_main

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def git(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, env=env, check=True,
                          stdin=subprocess.DEVNULL).stdout.strip()


class WorktreeCase(SessionTestCase):
    def setUp(self):
        super().setUp()
        self.repo = self.project["path"]
        git(self.repo, "init", "-q", "-b", "main")
        with open(os.path.join(self.repo, "a.txt"), "w") as f:
            f.write("a\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "init")

    def open(self, *extra):
        return run_main("open", "--project", self.slug, "--purpose", "build the api", "--title", "fm-api",
                        "--transport", "fake", "--prompt", "go", *extra)

    def open_one(self, *extra):
        code, out, err = self.open(*extra)
        self.assertEqual(code, 0, err)
        (rec,) = [r for r in self.sessions() if r["title"] == "fm-api"]
        return rec

    def worktrees(self):
        return [l.split(" ", 1)[1] for l in git(self.repo, "worktree", "list", "--porcelain").splitlines()
                if l.startswith("worktree ")]


class OpenWorktreeTest(WorktreeCase):
    def test_worker_gets_its_own_worktree_on_a_new_branch(self):
        from foreman import paths
        rec = self.open_one()
        wt = rec["cwd"]
        self.assertNotEqual(wt, self.repo)
        self.assertTrue(wt.startswith(os.path.realpath(paths.worktrees_dir(self.slug)) + os.sep), wt)
        self.assertIn(wt, self.worktrees())
        self.assertEqual(rec["worktree"]["path"], wt)
        branch = rec["worktree"]["branch"]
        self.assertTrue(branch.startswith("foreman/"), branch)
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "HEAD"), branch)
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main")  # main checkout untouched
        (term,) = self.fake.list()
        self.assertEqual(term.cwd, wt)  # the agent is launched in the worktree

    def test_two_workers_do_not_share_a_checkout(self):
        a = self.open_one()
        code, _, err = run_main("open", "--project", self.slug, "--purpose", "p", "--title", "fm-b",
                                "--transport", "fake", "--prompt", "go")
        self.assertEqual(code, 0, err)
        b = [r for r in self.sessions() if r["title"] == "fm-b"][0]
        self.assertNotEqual(a["cwd"], b["cwd"])
        self.assertNotEqual(a["worktree"]["branch"], b["worktree"]["branch"])

    def test_hook_file_is_written_into_the_worktree(self):
        rec = self.open_one("--agent", "claude")
        with open(os.path.join(rec["cwd"], ".claude", "settings.local.json")) as f:
            data = json.load(f)
        self.assertIn("hook --agent claude --event Stop", json.dumps(data))
        self.assertEqual(git(rec["cwd"], "status", "--porcelain"), "")  # excluded: the worktree stays clean

    def test_journal_names_the_worktree(self):
        rec = self.open_one()
        (line,) = [r for r in self.journal() if r.get("kind") == "open"]
        self.assertEqual(line["data"]["worktree"], rec["cwd"])

    def test_no_worktree_flag(self):
        rec = self.open_one("--no-worktree")
        self.assertEqual(rec["cwd"], self.repo)
        self.assertNotIn("worktree", rec)
        self.assertEqual(self.worktrees(), [self.repo])

    def test_orchestrator_and_user_owned_sessions_are_unchanged(self):
        code, _, err = run_main("open", "--project", self.slug, "--purpose", "p", "--title", "fm-u",
                                "--transport", "fake", "--prompt", "go", "--owner", "user")
        self.assertEqual(code, 0, err)
        code, _, err = run_main("open", "--project", self.slug, "--purpose", "p", "--title", "fm-o",
                                "--transport", "fake", "--prompt", "go", "--role", "orchestrator")
        self.assertEqual(code, 0, err)
        for rec in self.sessions():
            self.assertEqual(rec["cwd"], self.repo)
            self.assertNotIn("worktree", rec)
        self.assertEqual(self.worktrees(), [self.repo])

    def test_a_project_that_is_not_a_git_repository_is_unchanged(self):
        other = self.init_project("plain")
        code, _, err = run_main("open", "--project", other["slug"], "--purpose", "p", "--title", "fm-p",
                                "--transport", "fake", "--prompt", "go")
        self.assertEqual(code, 0, err)
        from foreman import sessions
        (rec,) = sessions.list_sessions(other["slug"])
        self.assertEqual(rec["cwd"], other["path"])
        self.assertNotIn("worktree", rec)

    def test_git_failure_falls_back_to_the_project_folder_with_a_note(self):
        from unittest import mock
        from foreman import worktrees
        with mock.patch.object(worktrees, "is_repo_root", return_value=True), \
                mock.patch.object(worktrees, "_git", side_effect=worktrees.WorktreeError("boom")):
            code, out, err = self.open()
        self.assertEqual(code, 0, err)
        self.assertIn("no worktree", out)
        (rec,) = self.sessions()
        self.assertEqual(rec["cwd"], self.repo)


class VisibilityTest(WorktreeCase):
    def test_project_for_path_finds_the_project_from_the_worktree(self):
        from foreman import projects
        rec = self.open_one()
        self.assertEqual(projects.project_for_path(rec["cwd"])["slug"], self.slug)
        self.assertEqual(projects.project_for_path(os.path.join(rec["cwd"], "sub", "dir"))["slug"], self.slug)

    def test_a_folder_beside_the_worktrees_belongs_to_no_project(self):
        from foreman import paths, projects
        self.open_one()
        self.assertIsNone(projects.project_for_path(os.path.join(paths.worktrees_dir("nope"), "x")))

    def test_cli_in_the_worktree_resolves_the_project(self):
        rec = self.open_one()
        os.chdir(rec["cwd"])
        code, out, err = run_main("status", "--json")
        self.assertEqual(code, 0, err)

    def test_a_session_started_by_hand_in_the_worktree_is_registered_under_the_project(self):
        from foreman import hooks
        rec = self.open_one()
        payload = {"session_id": "by-hand-1", "cwd": rec["cwd"], "hook_event_name": "UserPromptSubmit",
                   "transcript_path": None}
        hooks.run_hook("claude", "UserPromptSubmit", json.dumps(payload), env={})
        found = [r for r in self.sessions() if r.get("agent_session_id") == "by-hand-1"]
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["project"], found[0]["owner"]), (self.slug, "user"))


class CloseWorktreeTest(WorktreeCase):
    def close(self, ref="fm-api"):
        return run_main("close", "--project", self.slug, ref)

    def test_clean_worktree_is_removed_and_its_unused_branch_deleted(self):
        rec = self.open_one()
        wt, branch = rec["cwd"], rec["worktree"]["branch"]
        code, out, err = self.close()
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(wt))
        self.assertNotIn(wt, self.worktrees())
        self.assertNotIn(branch, git(self.repo, "branch", "--format=%(refname:short)").split())
        self.assertIn("removed worktree", out)

    def test_worktree_with_uncommitted_work_is_kept(self):
        rec = self.open_one()
        with open(os.path.join(rec["cwd"], "new.txt"), "w") as f:
            f.write("work\n")
        code, out, err = self.close()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(rec["cwd"], "new.txt")))
        self.assertIn("kept worktree", out)
        self.assertIn(rec["cwd"], out)

    def test_committed_work_keeps_the_branch(self):
        rec = self.open_one()
        wt, branch = rec["cwd"], rec["worktree"]["branch"]
        with open(os.path.join(wt, "b.txt"), "w") as f:
            f.write("b\n")
        git(wt, "add", "b.txt")
        git(wt, "commit", "-q", "-m", "work")
        code, out, err = self.close()
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(wt))  # the checkout goes, the commits stay on the branch
        self.assertIn(branch, git(self.repo, "branch", "--format=%(refname:short)").split())
        self.assertIn("kept branch", out)

    def test_session_without_a_worktree_closes_as_before(self):
        rec = self.open_one("--no-worktree")
        code, out, err = self.close()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "closed %s (fm-api)" % rec["sid"])

    def test_a_worktree_still_used_by_another_open_session_is_not_removed(self):
        rec = self.open_one()
        from foreman import sessions
        twin = self.new_session(title="twin", cwd=rec["cwd"], worktree=rec["worktree"])
        code, out, err = self.close()
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.isdir(rec["cwd"]))
        self.assertIn("still used", out)
        self.assertTrue(sessions.is_open(sessions.load_session(twin["sid"])))


class RotateWorktreeTest(WorktreeCase):
    def test_the_replacement_continues_in_the_same_worktree(self):
        rec = self.open_one()
        from foreman import sessions
        sessions.update_session(rec["sid"], state="idle")
        code, out, err = run_main("rotate", "--project", self.slug, "fm-api")
        self.assertEqual(code, 0, err)
        new = [r for r in self.sessions() if r["sid"] != rec["sid"]][0]
        self.assertEqual(new["cwd"], rec["cwd"])
        self.assertEqual(new["worktree"], rec["worktree"])
        self.assertEqual(len(self.worktrees()), 2)  # main + the one shared worktree


if __name__ == "__main__":
    unittest.main()
