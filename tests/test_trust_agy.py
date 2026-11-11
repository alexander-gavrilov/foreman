"""Antigravity trust at `init` (spec 5.5): the project folder's resolved path in trustedWorkspaces, add-only."""
import json
import os
import stat

from tests.trustbase import TrustCase, git, read, readb, write
from foreman import paths, projects, trust  # noqa: E402

HOOKS = ".agents/hooks.json"
USER_FILE = '{\n  "theme": "dark",\n  "trustedWorkspaces": [\n    "/home/x",\n    "/home/x/projects"\n  ],\n  "statusLine": {"command": "x"}\n}\n'


class AgyInitTest(TrustCase):
    def test_adds_the_resolved_folder_to_an_existing_list_and_keeps_the_rest(self):
        write(self.agy, USER_FILE)
        changes, notes, failures = self.init(agents=["antigravity"])
        self.assertEqual(failures, [])
        text = read(self.agy)
        self.assertEqual(json.loads(text)["trustedWorkspaces"], ["/home/x", "/home/x/projects", self.repo])
        self.assertEqual(text.replace(',\n    "%s"' % self.repo, ""), USER_FILE)  # only the added line differs
        self.assertEqual(readb(self.agy + ".foreman-bak"), USER_FILE.encode())
        (entry,) = self.record()
        self.assertEqual((entry["kind"], entry["value"], entry["state"], entry["scope"], entry["project"]),
                         ("agy-workspace", self.repo, "applied", "project", self.slug))
        self.assertTrue(any("Antigravity: trusted %s" % self.repo in c for c in changes), changes)
        self.assertTrue(any("every hook file" in c for c in changes))  # what the trust switches on is printed
        self.assertTrue(trust.consent(self.slug, "antigravity"))

    def test_a_link_to_the_folder_is_resolved_and_only_the_resolved_path_is_written(self):
        link = os.path.join(self.work, "applink")
        os.symlink(self.repo, link)
        self.init(agents=["antigravity"], path=link)
        self.assertEqual(self.agy_list(), [self.repo])

    def test_second_init_writes_nothing_and_makes_no_new_backup(self):
        write(self.agy, USER_FILE)
        self.init(agents=["antigravity"])
        before = (readb(self.agy), os.stat(self.agy).st_mtime_ns, readb(self.agy + ".foreman-bak"),
                  readb(os.path.join(self.state, "trust.json")))
        changes, notes, failures = self.init(agents=["antigravity"])
        after = (readb(self.agy), os.stat(self.agy).st_mtime_ns, readb(self.agy + ".foreman-bak"),
                 readb(os.path.join(self.state, "trust.json")))
        self.assertEqual(before, after)
        self.assertEqual(failures, [])
        self.assertFalse([c for c in changes if "trusted" in c])

    def test_an_entry_that_was_already_there_is_left_alone_and_is_not_foremans(self):
        write(self.agy, json.dumps({"trustedWorkspaces": [self.repo]}))
        original = readb(self.agy)
        changes, _, failures = self.init(agents=["antigravity"])
        self.assertEqual((failures, readb(self.agy)), ([], original))
        self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))
        self.assertEqual(self.record(), [])  # not recorded: foreman will never remove it
        self.assertTrue(trust.consent(self.slug, "antigravity"))  # consent granted when the entry was already there

    def test_broad_parent_entries_are_neither_relied_on_nor_removed(self):
        write(self.agy, json.dumps({"trustedWorkspaces": [os.path.dirname(self.repo), self.home]}))
        self.init(agents=["antigravity"])
        self.assertEqual(self.agy_list(), [os.path.dirname(self.repo), self.home, self.repo])

    def test_a_missing_file_is_created_0600_with_only_this_key(self):
        self.assertFalse(os.path.exists(self.agy))
        _, _, failures = self.init(agents=["antigravity"])
        self.assertEqual(failures, [])
        self.assertEqual(json.loads(read(self.agy)), {"trustedWorkspaces": [self.repo]})
        self.assertEqual(stat.S_IMODE(os.stat(self.agy).st_mode), 0o600)
        self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))

    def test_a_missing_folder_is_a_failure_and_nothing_is_created(self):
        os.rmdir(self.agy_dir)
        _, notes, failures = self.init(agents=["antigravity"])
        self.assertTrue(failures and "Antigravity" in failures[0])
        self.assertFalse(os.path.exists(self.agy_dir))
        self.assertFalse(trust.consent(self.slug, "antigravity"))

    def test_invalid_or_wrong_shape_files_are_left_exactly_as_they_were(self):
        for text in ("{nope", '{"trustedWorkspaces": "x"}', '{"trustedWorkspaces": [1, 2]}', "[]", '﻿{}',
                     '{"a": 1} // c', ""):
            with self.subTest(text):
                write(self.agy, text)
                changes, notes, failures = self.init(agents=["antigravity"])
                self.assertTrue(failures, "should fail for %r" % text)
                self.assertEqual(read(self.agy), text)
                self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))
                self.assertEqual([n for n in os.listdir(self.agy_dir)], ["settings.json"])
                self.assertEqual(self.record(), [])
                self.assertFalse(trust.consent(self.slug, "antigravity"))
                self.assertTrue(os.path.exists(os.path.join(self.repo, HOOKS)))  # init did everything else

    def test_an_unwritable_file_is_reported(self):
        write(self.agy, "{}")
        os.chmod(self.agy_dir, 0o500)
        self.addCleanup(os.chmod, self.agy_dir, 0o700)
        if os.access(self.agy_dir, os.W_OK):
            self.skipTest("running as a user that ignores modes")
        _, _, failures = self.init(agents=["antigravity"])
        self.assertTrue(failures)
        self.assertEqual(read(self.agy), "{}")

    def test_a_linked_settings_file_is_edited_at_its_target(self):
        real = os.path.join(self.home, "dotfiles", "settings.json")
        write(real, "{}")
        os.symlink(real, self.agy)
        self.init(agents=["antigravity"])
        self.assertTrue(os.path.islink(self.agy))
        self.assertEqual(json.loads(read(real))["trustedWorkspaces"], [self.repo])
        self.assertTrue(os.path.exists(real + ".foreman-bak"))

    def test_a_link_whose_target_is_outside_the_home_is_written_only_for_the_accounts_own_home(self):
        real = os.path.join(self.work, "dotfiles", "settings.json")  # a trial HOME is not the account's own home
        write(real, "{}")
        os.symlink(real, self.agy)
        _, notes, failures = self.init(agents=["antigravity"])
        self.assertEqual((failures, read(real)), ([], "{}"))
        self.assertTrue(any("outside the HOME in effect" in n for n in notes), notes)

    def test_a_control_character_in_the_path_is_refused(self):
        weird = self.make_dir("we\nird")
        _, _, failures = self.init(agents=["antigravity"], path=weird)
        self.assertTrue(failures)
        self.assertIsNone(self.agy_list())

    def test_only_a_project_folder_is_added_by_init_and_only_for_the_agents_with_hooks(self):
        self.init(agents=["claude"])
        self.assertIsNone(self.agy_list())  # no Antigravity hooks, no Antigravity trust
        self.assertFalse(os.path.exists(os.path.join(self.state, "trust.json")))

    def test_unrelated_trial_state_folder_writes_nothing(self):
        os.environ["FOREMAN_HOME"] = os.path.join(self.tmp, "throwaway")
        os.makedirs(os.environ["FOREMAN_HOME"])
        changes, notes, failures = self.init(agents=["antigravity"])
        self.assertEqual(failures, [])
        self.assertIsNone(self.agy_list())
        self.assertTrue(any("trust not written" in n and "trust the workspace in Antigravity" in n for n in notes), notes)

    def test_a_redirected_home_cannot_reach_a_codex_home_elsewhere(self):
        elsewhere = os.path.join(self.tmp, "real-codex")
        os.makedirs(elsewhere)
        write(os.path.join(elsewhere, "config.toml"), "")
        self.setenv("CODEX_HOME", elsewhere)
        changes, notes, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        self.assertEqual(read(os.path.join(elsewhere, "config.toml")), "")
        self.assertTrue(any("outside the HOME in effect" in n for n in notes), notes)


class AgyPathRulesTest(TrustCase):
    def refused(self, path, **kw):
        with self.assertRaises(trust.TrustRefused) as cm:
            trust.check_path(path, **kw)
        return cm.exception.reason

    def test_root_home_and_what_is_above_them(self):
        self.assertIn("root", self.refused("/"))
        self.assertIn("home folder", self.refused(self.home))
        self.assertIn("home folder", self.refused(os.path.dirname(self.home)))
        link = os.path.join(self.work, "homelink")
        os.symlink(self.home, link)
        self.assertIn("home folder", self.refused(link))

    def test_the_state_folder_and_what_is_above_or_inside_it(self):
        for p in (self.state, os.path.dirname(self.state), os.path.join(self.state, "projects")):
            self.assertIn("state folder", self.refused(p))

    def test_the_agents_configuration_folders_and_above_them(self):
        self.assertIn("configuration folder", self.refused(self.agy_dir))
        self.assertIn("configuration folder", self.refused(self.codex_dir))
        self.assertIn("configuration folder", self.refused(os.path.dirname(self.agy_dir)))  # ~/.gemini

    def test_a_folder_of_repositories_is_refused_whatever_is_registered(self):
        # as the first project: no repository is registered yet
        self.assertIn("folder of repositories", self.refused(self.work))
        # and after its children were initialised
        projects.create_project(self.repo)
        self.assertIn("contains another registered project", self.refused(self.work))

    def test_the_rule_does_not_depend_on_the_order_of_inits(self):
        other = self.make_dir("other")
        git(other, "init", "-q")
        self.assertIn("folder of repositories", self.refused(self.work))
        projects.create_project(other)
        projects.create_project(self.repo)
        self.assertIn("contains another registered project", self.refused(self.work))

    def test_a_monorepo_root_is_a_repository_and_is_allowed(self):
        mono = self.make_dir("mono")
        git(mono, "init", "-q")
        git(self.make_dir("mono/pkg"), "init", "-q")  # a nested repository inside the repository
        self.assertEqual(trust.check_path(mono), mono)

    def test_a_plain_repository_is_allowed(self):
        self.assertEqual(trust.check_path(self.repo), self.repo)

    def test_init_refuses_the_home_folder_and_a_folder_of_repositories_end_to_end(self):
        for path in (self.home, self.work):
            with self.subTest(path):
                write(self.agy, USER_FILE)
                changes, notes, failures = self.init(agents=["antigravity"], path=path)
                self.assertTrue(failures and "refused" in failures[0], failures)
                self.assertEqual(read(self.agy), USER_FILE)
                self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))
                self.assertEqual(self.record(), [])

    def test_worktree_rules(self):
        proj = projects.create_project(self.repo)
        slug = proj["slug"]
        root = paths.worktrees_dir(slug)
        os.makedirs(root)
        good = os.path.join(root, "w1")
        git(self.repo, "worktree", "add", "-q", "-b", "b1", good)
        good = os.path.realpath(good)
        self.assertEqual(trust.check_path(good, worktree_of=slug, expect=good), good)
        with self.assertRaises(trust.TrustRefused):  # not the path the session's record names
            trust.check_path(good, worktree_of=slug, expect=os.path.join(root, "other"))
        deeper = os.path.join(good, "sub")
        os.makedirs(deeper)
        with self.assertRaises(trust.TrustRefused):  # not a direct child
            trust.check_path(deeper, worktree_of=slug, expect=deeper)
        stray = os.path.join(root, "stray")
        os.makedirs(stray)
        with self.assertRaises(trust.TrustRefused):  # not listed by `git worktree list`
            trust.check_path(stray, worktree_of=slug, expect=stray)
        outside = os.path.join(self.work, "elsewhere")
        git(self.repo, "worktree", "add", "-q", "-b", "b2", outside)
        with self.assertRaises(trust.TrustRefused):  # listed, but not under the project's worktrees folder
            trust.check_path(outside, worktree_of=slug, expect=outside)
