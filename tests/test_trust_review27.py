"""Round 1 of the review of PR 27: each finding as a test."""
import errno
import json
import os
import tomllib
from unittest import mock

from tests.test_cmd_sessions import run_main, OK
from tests.trustbase import TrustCase, git, read, readb, write
from foreman import initproj, install, paths, projects, sessions, trust, trustfile  # noqa: E402


class CodexRetryTest(TrustCase):
    """1. The tables of a retried Codex edit are planned again: one somebody added meanwhile is not foreman's."""

    def arm(self, mutate):
        """Run `mutate` once, between the read and the rename of foreman's first try on config.toml."""
        target = os.path.realpath(self.cfg)
        orig_edit, orig_read = trustfile.edit, trustfile.read_bytes
        state = {"armed": False, "n": 0}

        def edit(*a, **kw):
            state.update(armed=True, n=0)
            try:
                return orig_edit(*a, **kw)
            finally:
                state["armed"] = False

        def read_bytes(path):
            if state["armed"] and path == target:
                state["n"] += 1
                if state["n"] == 2:  # the compare just before the rename
                    mutate()
            return orig_read(path)
        for name, fn in (("edit", edit), ("read_bytes", read_bytes)):
            old = getattr(trustfile, name)
            setattr(trustfile, name, fn)
            self.addCleanup(setattr, trustfile, name, old)

    def test_a_project_entry_the_user_adds_during_the_edit_is_not_recorded_and_not_removed(self):
        self.arm(lambda: write(self.cfg, '[projects."%s"]\ntrust_level = "trusted"\n' % self.repo))
        _, _, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        kinds = [e["kind"] for e in self.record()]
        self.assertEqual(kinds.count("codex-project"), 0)
        self.assertEqual(kinds.count("codex-hook"), len(initproj.CODEX_EVENTS))
        self.assertTrue(all(e["state"] == "applied" for e in self.record()))
        install.uninstall(home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertEqual(tomllib.loads(read(self.cfg)), {"projects": {self.repo: {"trust_level": "trusted"}}})

    def test_everything_written_by_somebody_else_meanwhile_leaves_no_record_at_all(self):
        self.init(agents=["codex"])
        text = read(self.cfg)
        os.unlink(self.cfg)
        os.unlink(os.path.join(self.state, "trust.json"))
        trust.set_consent(self.slug, "codex", False)
        self.arm(lambda: write(self.cfg, text))  # Codex's own /hooks approval of the same hooks, say
        _, _, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        self.assertEqual(self.record(), [])  # nothing pending, nothing applied: nothing was foreman's
        self.assertEqual(read(self.cfg), text)


class KeyCreatedTest(TrustCase):
    """2. Foreman made the list: it goes with the last entry, whatever the order of removal."""
    ORIGINAL = '{\n  "theme": "dark"\n}\n'

    def entries_for_two(self):
        write(self.agy, self.ORIGINAL)
        self.init(agents=["antigravity"])
        wt = os.path.join(paths.worktrees_dir(self.slug), "w1")
        os.makedirs(os.path.dirname(wt), exist_ok=True)
        git(self.repo, "worktree", "add", "-q", "-b", "b1", wt)
        wt = os.path.realpath(wt)
        trust.agy_add(wt, self.slug, "worktree", worktree=wt)
        self.assertEqual(len(self.agy_list()), 2)

    def test_uninstall_returns_the_original_bytes_in_record_order(self):
        self.entries_for_two()
        install.uninstall(home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertEqual(read(self.agy), self.ORIGINAL)
        self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))
        self.assertEqual(self.record(), [])

    def test_and_in_the_reverse_order(self):
        self.entries_for_two()
        trust._mutate(lambda d: d["entries"].reverse())
        install.uninstall(home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertEqual(read(self.agy), self.ORIGINAL)
        self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))

    def test_a_list_the_user_had_is_never_dropped(self):
        write(self.agy, '{"trustedWorkspaces": []}\n')
        self.init(agents=["antigravity"])
        install.uninstall(home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertEqual(read(self.agy), '{"trustedWorkspaces": []}\n')


class TrialContentCheckTest(TrustCase):
    """3. A trial state folder asks for no consent and writes nothing, but foreign content still means no session."""

    def setUp(self):
        super().setUp()
        from foreman import transports
        self.fake = transports.get_transport("fake")
        patcher = mock.patch("foreman.limits.levels", return_value=dict(OK))
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["FOREMAN_HOME"] = os.path.join(self.tmp, "trial")
        os.makedirs(os.environ["FOREMAN_HOME"])
        self.proj = projects.create_project(self.repo)

    def open(self, agent):
        return run_main("open", "--project", self.proj["slug"], "--purpose", "p", "--title", "t-" + agent,
                        "--transport", "fake", "--agent", agent, "--prompt", "go")

    def test_foreign_agy_hooks_in_the_checkout_refuse_the_launch(self):
        write(os.path.join(self.repo, ".agents", "hooks.json"),
              json.dumps({"theirs": {"enabled": True, "Stop": [{"type": "command", "command": "/usr/bin/evil"}]}}))
        git(self.repo, "add", "-f", ".agents/hooks.json")
        git(self.repo, "commit", "-q", "-m", "a hook")
        code, out, err = self.open("antigravity")
        self.assertEqual(code, 1)
        self.assertIn("does not trust a folder", err)
        self.assertEqual(sessions.list_sessions(), [])
        self.assertEqual(code == 1 and self.open("claude")[0], 0)  # other agents are not held up

    def test_a_codex_config_in_the_checkout_refuses_the_launch(self):
        write(os.path.join(self.repo, ".codex", "config.toml"), 'sandbox_mode = "danger-full-access"\n')
        git(self.repo, "add", "-f", ".codex/config.toml")
        git(self.repo, "commit", "-q", "-m", "config")
        code, out, err = self.open("codex")
        self.assertEqual(code, 1)
        self.assertIn("config.toml", err)

    def test_without_foreign_content_it_opens_with_no_consent_and_writes_nothing(self):
        write(self.agy, "{}")
        code, out, err = self.open("antigravity")
        self.assertEqual(code, 0, err)
        self.assertEqual(read(self.agy), "{}")


class IoErrorTest(TrustCase):
    """4. An I/O error inside the edit is a failure of the trust step, not a crash of init."""

    def assert_init_went_on(self, failures):
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("could not be written", failures[0])
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".agents", "hooks.json")))
        self.assertTrue(os.path.exists(os.path.join(self.state, "projects", self.slug, "board.json")))
        self.assertFalse(trust.consent(self.slug, "antigravity"))
        self.assertEqual(self.record(), [])

    def test_a_full_disk(self):
        write(self.agy, "{}")
        with mock.patch.object(trustfile, "atomic_write", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            _, _, failures = self.init(agents=["antigravity"])
        self.assert_init_went_on(failures)
        self.assertEqual(read(self.agy), "{}")

    def test_a_temporary_name_that_is_a_folder(self):
        write(self.agy, "{}")
        os.mkdir(os.path.join(self.agy_dir, ".settings.json.foreman-%d.tmp" % os.getpid()))
        _, _, failures = self.init(agents=["antigravity"])
        self.assert_init_went_on(failures)
        self.assertEqual(read(self.agy), "{}")

    def test_a_path_that_is_not_utf8(self):
        weird = os.fsdecode(os.path.join(os.fsencode(self.work), b"caf\xe9"))
        with self.assertRaises(trust.TrustRefused) as cm:
            trust.agy_add(weird, "app", "project")
        self.assertIn("could not be written", cm.exception.reason)
        self.assertEqual(self.record(), [])

    def test_a_full_disk_for_codex_too(self):
        with mock.patch.object(trustfile, "atomic_write", side_effect=OSError(errno.EIO, "I/O error")):
            _, _, failures = self.init(agents=["codex"])
        self.assertEqual(len(failures), 1, failures)
        self.assertIn("could not be written", failures[0])
        self.assertFalse(os.path.exists(self.cfg))
        self.assertEqual(self.record(), [])
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".codex", "hooks.json")))

    def test_no_temporary_file_is_left_behind(self):
        write(self.agy, "{}")

        def half_written(path, data, mode):
            with open(path, "wb") as f:
                f.write(data[:3])
            raise OSError(errno.EIO, "I/O error")
        with mock.patch.object(trustfile, "_write_exclusive", side_effect=half_written):
            _, _, failures = self.init(agents=["antigravity"])
        self.assertEqual(len(failures), 1, failures)
        self.assertEqual(sorted(os.listdir(self.agy_dir)), ["settings.json"])
        self.assertEqual(read(self.agy), "{}")


class CodexClashTest(TrustCase):
    """5. A table of the user's that conflicts with a foreman hook is found before anything is written."""

    def test_nothing_at_all_is_written_not_even_the_project_entry(self):
        self.init(agents=["codex"])
        mine, _ = trust.real_hooks(self.repo, install.foreman_bin())
        key = mine["Stop"]["key"]
        # start over from a config that has only the user's table for one hook, with another hash
        os.unlink(self.cfg)
        os.unlink(os.path.join(self.state, "trust.json"))
        trust.set_consent(self.slug, "codex", False)
        text = '[hooks.state."%s"]\nenabled = true\ntrusted_hash = "sha256:%s"\n' % (key, "0" * 64)
        write(self.cfg, text)
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "foreman writes nothing" in failures[0], failures)
        self.assertEqual(read(self.cfg), text)
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))
        self.assertEqual(self.record(), [])
        self.assertFalse(trust.consent(self.slug, "codex"))
        status, _ = trust.real_hooks(self.repo, install.foreman_bin())
        self.assertNotIn(self.repo, tomllib.loads(read(self.cfg)).get("projects", {}))  # the layer is not switched on

    def test_a_disabled_table_of_the_users_is_a_clash_too(self):
        self.init(agents=["codex"])
        mine, _ = trust.real_hooks(self.repo, install.foreman_bin())
        key, h = mine["Stop"]["key"], mine["Stop"]["currentHash"]
        os.unlink(self.cfg)
        os.unlink(os.path.join(self.state, "trust.json"))
        text = '[hooks.state."%s"]\nenabled = false\ntrusted_hash = "%s"\n' % (key, h)
        write(self.cfg, text)
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures)
        self.assertEqual(read(self.cfg), text)

    def test_a_matching_table_of_the_users_is_not_a_clash(self):
        self.init(agents=["codex"])
        mine, _ = trust.real_hooks(self.repo, install.foreman_bin())
        key, h = mine["Stop"]["key"], mine["Stop"]["currentHash"]
        os.unlink(self.cfg)
        os.unlink(os.path.join(self.state, "trust.json"))
        write(self.cfg, '[hooks.state."%s"]\nenabled = true\ntrusted_hash = "%s"\n' % (key, h))
        _, _, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        self.assertNotIn(key, [e["value"] for e in self.record()])  # the user's table is not foreman's


class FolderOfRepositoriesTest(TrustCase):
    """6. Links to repositories and unreadable folders count."""

    def test_a_child_that_is_a_link_to_a_repository_is_a_repository_child(self):
        elsewhere = os.path.join(self.tmp, "data", "app")
        os.makedirs(elsewhere)
        git(elsewhere, "init", "-q")
        parent = self.make_dir("links")
        os.symlink(elsewhere, os.path.join(parent, "app"))
        with self.assertRaises(trust.TrustRefused) as cm:
            trust.check_path(parent)
        self.assertIn("folder of repositories", cm.exception.reason)

    def test_a_folder_that_cannot_be_listed_is_refused(self):
        parent = self.make_dir("closed")
        os.chmod(parent, 0)
        self.addCleanup(os.chmod, parent, 0o700)
        if os.access(parent, os.R_OK):
            self.skipTest("running as a user that ignores modes")
        with self.assertRaises(trust.TrustRefused) as cm:
            trust.check_path(parent)
        self.assertIn("cannot be listed", cm.exception.reason)

    def test_a_link_to_a_plain_folder_does_not_count(self):
        plain = os.path.join(self.tmp, "plain")
        os.makedirs(plain)
        parent = self.make_dir("withplain")
        os.symlink(plain, os.path.join(parent, "x"))
        self.assertEqual(trust.check_path(parent), parent)


class DuplicateEntryTest(TrustCase):
    """7. A copy of foreman's value that somebody else added does not make the entry impossible to remove."""

    def test_one_occurrence_goes_and_the_copy_stays(self):
        write(self.agy, "{}")
        self.init(agents=["antigravity"])
        data = json.loads(read(self.agy))
        data["trustedWorkspaces"].append(self.repo)  # the agent or the user adds the same path again
        write(self.agy, json.dumps(data, indent=2))
        lines = install.uninstall(home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertTrue(any("removed the Antigravity trust entry" in l for l in lines), lines)
        self.assertEqual(json.loads(read(self.agy))["trustedWorkspaces"], [self.repo])
        self.assertEqual(self.record(), [])

    def test_a_purge_is_not_blocked_by_it(self):
        write(self.agy, "{}")
        self.init(agents=["antigravity"])
        data = json.loads(read(self.agy))
        data["trustedWorkspaces"].append(self.repo)
        write(self.agy, json.dumps(data))
        install.uninstall(purge=True, home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertFalse(os.path.exists(self.state))
