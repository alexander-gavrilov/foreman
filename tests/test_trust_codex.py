"""Codex trust (spec 5.5): `[projects."<folder>"]` and one `[hooks.state."<key>"]` table per foreman hook, with the hash
Codex itself reports; per repository, so a worktree needs none. The real Codex is replaced by tests/stubs/codex."""
import json
import os
import tomllib

from tests.trustbase import TrustCase, git, read, readb, write
from foreman import initproj, install, projects, trust, trustfile  # noqa: E402

CFG = '# my codex\nmodel = "o3"\n\n[tui]\nstatus = true\n'


def parsed(path):
    return tomllib.loads(read(path))


class CodexBase(TrustCase):
    def hooks_status(self, folder=None):
        mine, look = trust.real_hooks(folder or self.repo, install.foreman_bin())
        return {e: h["trustStatus"] for e, h in mine.items()}, look

    def tables(self):
        return (parsed(self.cfg).get("hooks") or {}).get("state") or {}


class CodexInitTest(CodexBase):
    def test_writes_the_project_entry_and_one_table_per_hook_with_codexs_own_hash(self):
        write(self.cfg, CFG)
        changes, notes, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        cfg = parsed(self.cfg)
        self.assertEqual(cfg["model"], "o3")
        self.assertEqual(cfg["projects"][self.repo], {"trust_level": "trusted"})
        self.assertEqual(len(cfg["hooks"]["state"]), len(initproj.CODEX_EVENTS))
        for key, row in cfg["hooks"]["state"].items():
            self.assertEqual((row["enabled"], row["trusted_hash"][:7]), (True, "sha256:"))
            self.assertTrue(key.startswith(os.path.join(self.repo, ".codex", "hooks.json:")), key)
        self.assertTrue(read(self.cfg).startswith(CFG))  # the old text plus added lines: comments survive
        status, look = self.hooks_status()
        self.assertEqual(set(status.values()), {"trusted"})
        self.assertEqual((len(status), look), (len(initproj.CODEX_EVENTS), []))
        self.assertEqual(readb(self.cfg + ".foreman-bak"), CFG.encode())
        kinds = sorted(e["kind"] for e in self.record())
        self.assertEqual(kinds, ["codex-hook"] * len(initproj.CODEX_EVENTS) + ["codex-project"])
        self.assertTrue(all(e["state"] == "applied" for e in self.record()))
        self.assertTrue(any("whole .codex config layer" in c for c in changes), changes)  # what trust switches on
        self.assertTrue(trust.consent(self.slug, "codex"))

    def test_the_hash_is_the_one_codex_reports_not_a_copy_of_the_recipe(self):
        self.init(agents=["codex"])
        mine, _ = trust.real_hooks(self.repo, install.foreman_bin())
        for h in mine.values():
            self.assertEqual(self.tables()[h["key"]]["trusted_hash"], h["currentHash"])

    def test_a_linked_worktree_needs_no_entry_of_its_own(self):
        self.init(agents=["codex"])
        wt = os.path.join(self.work, "wt")
        git(self.repo, "worktree", "add", "-q", "-b", "w", wt)
        initproj.write_hooks(wt, "codex", install.foreman_bin())  # ignored by Codex: it reads the main checkout's
        status, _ = self.hooks_status(os.path.realpath(wt))
        self.assertEqual(set(status.values()), {"trusted"})
        self.assertEqual(len(status), len(initproj.CODEX_EVENTS))

    def test_second_init_writes_nothing(self):
        write(self.cfg, CFG)
        self.init(agents=["codex"])
        before = (readb(self.cfg), os.stat(self.cfg).st_mtime_ns, readb(self.cfg + ".foreman-bak"),
                  readb(os.path.join(self.state, "trust.json")))
        _, _, failures = self.init(agents=["codex"])
        after = (readb(self.cfg), os.stat(self.cfg).st_mtime_ns, readb(self.cfg + ".foreman-bak"),
                 readb(os.path.join(self.state, "trust.json")))
        self.assertEqual((failures, before), ([], after))

    def test_a_missing_config_is_created_holding_only_the_added_tables(self):
        self.assertFalse(os.path.exists(self.cfg))
        _, _, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        cfg = parsed(self.cfg)
        self.assertEqual(sorted(cfg), ["hooks", "projects"])
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))
        self.assertEqual(oct(os.stat(self.cfg).st_mode & 0o777), "0o600")

    def test_codex_home_is_honoured(self):
        other = os.path.join(self.home, "elsewhere")
        os.makedirs(other)
        self.setenv("CODEX_HOME", other)
        self.init(agents=["codex"])
        self.assertTrue(os.path.exists(os.path.join(other, "config.toml")))
        self.assertFalse(os.path.exists(self.cfg))

    def test_the_users_own_entries_are_never_touched_or_recorded(self):
        write(self.cfg, '[projects."%s"]\ntrust_level = "trusted"\n' % self.repo)
        _, _, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        self.assertEqual([e["kind"] for e in self.record()].count("codex-project"), 0)  # not foreman's
        self.assertEqual(parsed(self.cfg)["projects"], {self.repo: {"trust_level": "trusted"}})

    def test_untrusted_is_the_users_decision_and_is_kept(self):
        text = '[projects."%s"]\ntrust_level = "untrusted"\n' % self.repo
        write(self.cfg, text)
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "untrusted" in failures[0], failures)
        self.assertEqual(read(self.cfg), text)
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))
        self.assertFalse(trust.consent(self.slug, "codex"))

    def test_a_project_entry_without_trust_level_is_not_edited(self):
        text = '[projects."%s"]\nother = 1\n' % self.repo
        write(self.cfg, text)
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "add it by hand" in failures[0], failures)
        self.assertEqual(read(self.cfg), text)

    def test_a_disabled_hook_of_the_users_stays_and_init_reports_the_project_as_not_trusted(self):
        self.init(agents=["codex"])
        key = sorted(self.tables())[0]
        text = read(self.cfg).replace('[hooks.state."%s"]\nenabled = true' % key, '[hooks.state."%s"]\nenabled = false' % key)
        write(self.cfg, text)
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "does not report foreman's hooks trusted" in failures[0], failures)  # not silent
        self.assertEqual(self.tables()[key]["enabled"], False)  # the user's decision is kept
        # and the table is not the one foreman wrote any more, so removal leaves it and says so
        lines = trust.remove_all()
        self.assertTrue(any("was changed since foreman wrote it" in l for l in lines), lines)
        self.assertIn(key, self.tables())

    def test_a_table_the_user_approved_for_an_older_hook_is_left_and_the_next_step_is_reported(self):
        self.init(agents=["codex"])
        mine, _ = trust.real_hooks(self.repo, install.foreman_bin())
        key = mine["Stop"]["key"]
        # forget foreman's record: the table now looks like the user's own, approved for an older hook
        trust._mutate(lambda d: d.__setitem__("entries", [e for e in d["entries"] if e["value"] != key]))
        write(self.cfg, read(self.cfg).replace(mine["Stop"]["currentHash"], "sha256:" + "0" * 64))
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "modified" not in failures[0] and "/hooks" in failures[0], failures)
        self.assertEqual(self.tables()[key]["trusted_hash"], "sha256:" + "0" * 64)  # never touched

    def test_a_table_foreman_recorded_with_a_wrong_hash_is_replaced_after_a_moved_install(self):
        self.init(agents=["codex"], fm="/old/place/foreman")
        old_keys = set(self.tables())
        _, _, failures = self.init(agents=["codex"], fm="/new/place/foreman")
        self.assertEqual(failures, [])
        self.assertEqual(len(self.tables()), len(initproj.CODEX_EVENTS))
        self.assertEqual({e["value"] for e in self.record() if e["kind"] == "codex-hook"}, set(self.tables()))
        mine, _ = trust.real_hooks(self.repo, "/new/place/foreman")
        self.assertEqual({h["trustStatus"] for h in mine.values()}, {"trusted"})
        self.assertEqual(old_keys, set(self.tables()))  # same keys (same file and slots), new hashes

    def test_invalid_and_wrong_shape_configs_are_left_exactly_as_they_were(self):
        for text in ("[unclosed", 'hooks = 1\n', 'projects = "x"\n', '[hooks]\nstate = 3\n'):
            with self.subTest(text):
                write(self.cfg, text)
                _, _, failures = self.init(agents=["codex"])
                self.assertTrue(failures, text)
                self.assertEqual(read(self.cfg), text)
                self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))
                self.assertEqual(self.record(), [])
                self.assertFalse(trust.consent(self.slug, "codex"))

    def test_an_inline_projects_table_cannot_be_extended_and_is_left_alone(self):
        text = 'projects = { "/x" = { trust_level = "trusted" } }\n'
        write(self.cfg, text)
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures)
        self.assertEqual(read(self.cfg), text)

    def test_a_path_with_a_quote_and_a_backslash_is_encoded_by_a_real_encoder(self):
        weird = self.make_dir('we"i\\rd')
        git(weird, "init", "-q")
        _, _, failures = self.init(agents=["codex"], path=weird)
        self.assertEqual(failures, [])
        self.assertIn(weird, parsed(self.cfg)["projects"])
        mine, _ = trust.real_hooks(weird, install.foreman_bin())
        self.assertEqual({h["trustStatus"] for h in mine.values()}, {"trusted"})

    def test_an_older_python_writes_no_codex_trust_and_says_so(self):
        orig = trustfile.toml_module
        trustfile.toml_module = lambda: None
        self.addCleanup(setattr, trustfile, "toml_module", orig)
        _, notes, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        self.assertTrue(any("Python 3.11" in n for n in notes), notes)
        self.assertFalse(os.path.exists(self.cfg))

    def test_without_codex_nothing_is_written_and_init_says_the_hooks_need_approval(self):
        os.unlink(os.path.join(self.codexbin, "codex"))
        os.environ["PATH"] = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep)
                                              if not os.path.exists(os.path.join(p, "codex")))
        _, notes, failures = self.init(agents=["codex"])
        self.assertEqual(failures, [])
        self.assertTrue(any("codex is not installed" in n and "/hooks" in n for n in notes), notes)
        self.assertFalse(os.path.exists(self.cfg))
        self.assertFalse(trust.consent(self.slug, "codex"))

    def test_a_codex_that_hangs_exits_or_answers_nonsense_writes_nothing(self):
        orig = trust.CODEX_TIMEOUT_S
        trust.CODEX_TIMEOUT_S = 1
        self.addCleanup(setattr, trust, "CODEX_TIMEOUT_S", orig)
        for mode, word in (("hang", "did not answer"), ("exit", "exited"), ("garbage", "shape")):
            with self.subTest(mode):
                self.setenv("CODEX_STUB_MODE", mode)
                _, notes, failures = self.init(agents=["codex"])
                self.assertEqual(failures, [])
                self.assertTrue(any(word in n and "/hooks" in n for n in notes), (mode, notes))
                self.assertFalse(os.path.exists(self.cfg))

    def test_init_in_a_linked_worktree_foreman_did_not_make_writes_no_codex_trust(self):
        projects.create_project(self.repo)
        wt = os.path.join(self.work, "orca-wt")
        git(self.repo, "worktree", "add", "-q", "-b", "o", wt)
        _, notes, failures = self.init(agents=["codex"], path=wt)
        self.assertEqual(failures, [])
        self.assertTrue(any("main checkout" in n for n in notes), notes)
        self.assertFalse(os.path.exists(self.cfg))

    def test_the_real_codex_home_is_only_asked_never_written_by_the_query(self):
        write(self.cfg, CFG)
        self.init(agents=["codex"])
        before = readb(self.cfg)
        self.hooks_status()
        self.assertEqual(readb(self.cfg), before)
        self.assertGreaterEqual(self.codex_calls(), 3)

    def test_look_alike_hooks_are_reported_and_never_approved(self):
        self.init(agents=["codex"])
        os.unlink(self.cfg)  # foreman created it, so there is no backup
        os.unlink(os.path.join(self.state, "trust.json"))
        path = os.path.join(self.repo, ".codex", "hooks.json")
        data = json.loads(read(path))
        data["hooks"]["Stop"].append({"hooks": [{"type": "command", "timeout": 10,
                                                  "command": "/tmp/evil/foreman hook --agent codex --event Stop"}]})
        write(path, json.dumps(data))
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "look like foreman's" in failures[0], failures)
        self.assertFalse(os.path.exists(self.cfg))

    def test_the_hook_file_is_not_foreign_content_but_anything_else_in_dot_codex_is(self):
        self.init(agents=["codex"])
        self.assertEqual(trust.foreign_codex(self.repo), {})
        write(os.path.join(self.repo, ".codex", "config.toml"), 'approval_policy = "never"\n')
        write(os.path.join(self.repo, ".codex", "rules", "x.rules"), "r\n")
        self.assertEqual(sorted(trust.foreign_codex(self.repo)), ["config.toml", "rules/x.rules"])


class CodexForeignContentTest(CodexBase):
    def test_a_repository_with_its_own_codex_config_is_manual_by_default(self):
        write(os.path.join(self.repo, ".codex", "config.toml"), 'sandbox_mode = "danger-full-access"\n')
        write(self.cfg, CFG)
        _, _, failures = self.init(agents=["codex"])
        self.assertEqual(len(failures), 1)
        self.assertIn("config.toml", failures[0])
        self.assertIn("your decision", failures[0])
        self.assertEqual(read(self.cfg), CFG)  # nothing written
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))
        self.assertFalse(trust.consent(self.slug, "codex"))
        self.assertEqual(self.record(), [])

    def test_rules_and_other_files_count_too(self):
        write(os.path.join(self.repo, ".codex", "rules", "default.rules"), "x\n")
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "rules/default.rules" in failures[0], failures)

    def test_user_trusted_by_hand_means_a_waiver_for_the_content_shown(self):
        self.init(agents=["codex"])
        # turn foreman's trust into the user's: the user approved the hooks by hand, nothing of it is foreman's record
        os.unlink(os.path.join(self.state, "trust.json"))
        project = projects.project_for_path(self.repo)
        trust.set_consent(project["slug"], "codex", False)
        write(os.path.join(self.repo, ".codex", "config.toml"), 'approval_policy = "never"\n')
        before = readb(self.cfg)
        changes, notes, failures = self.init(agents=["codex"])
        self.assertEqual((failures, readb(self.cfg)), ([], before))
        self.assertTrue(any("already trusted by you" in c and "config.toml" in c for c in changes), changes)
        waiver = trust.consent(project["slug"], "codex")["waiver"]
        self.assertEqual(list(waiver["units"]), ["config.toml"])
        fm = install.foreman_bin()
        slug = project["slug"]
        self.assertIsNone(trust.content_allowed(slug, "codex", self.repo, trust.foreign_codex(self.repo), fm, self.repo))
        write(os.path.join(self.repo, ".codex", "config.toml"), 'approval_policy = "never"\nsandbox_mode = "x"\n')
        self.assertIn("new or changed", trust.content_allowed(slug, "codex", self.repo, trust.foreign_codex(self.repo),
                                                              fm, self.repo))
        write(os.path.join(self.repo, ".codex", "config.toml"), 'approval_policy = "never"\n')
        write(os.path.join(self.repo, ".codex", "extra.toml"), "x = 1\n")
        self.assertIn("extra.toml", trust.content_allowed(slug, "codex", self.repo, trust.foreign_codex(self.repo),
                                                          fm, self.repo))
        os.unlink(os.path.join(self.repo, ".codex", "extra.toml"))
        write(self.cfg, "")  # the user's own entries are gone: the waiver lapses
        self.assertIn("your own trust entry", trust.content_allowed(slug, "codex", self.repo,
                                                                    trust.foreign_codex(self.repo), fm, self.repo))

    def test_foremans_own_trust_and_content_that_appears_later_waives_nothing(self):
        self.init(agents=["codex"])
        write(os.path.join(self.repo, ".codex", "config.toml"), 'sandbox_mode = "danger-full-access"\n')
        slug = self.slug
        fm = install.foreman_bin()
        self.assertIn("does not trust a folder", trust.content_allowed(slug, "codex", self.repo,
                                                                       trust.foreign_codex(self.repo), fm, self.repo))
        _, _, failures = self.init(agents=["codex"])
        self.assertTrue(failures and "foreman's own Codex trust is in place" in failures[0], failures)
        self.assertIn("foreman init --remove", failures[0])
        self.assertTrue(self.tables())  # foreman removes nothing by itself


class CodexRemovalTest(CodexBase):
    def test_init_remove_takes_the_tables_back_and_the_file_returns_to_its_bytes(self):
        write(self.cfg, CFG)
        self.init(agents=["codex"])
        lines = initproj.remove_project(self.repo)
        self.assertTrue(any("removed the Codex trust table" in l for l in lines), lines)
        self.assertEqual(read(self.cfg), CFG)
        self.assertEqual(self.record(), [])
        self.assertFalse(trust.consent(self.slug, "codex"))

    def test_uninstall_returns_a_created_file_to_an_empty_one(self):
        self.init(agents=["codex"])
        install.uninstall(home=self.home, platform="linux", runner=lambda argv: 0)
        self.assertEqual(read(self.cfg), "")
        self.assertEqual(self.record(), [])

    def test_the_users_own_tables_survive_a_removal(self):
        write(self.cfg, CFG + '\n[hooks.state."/elsewhere/hooks.json:stop:0:0"]\nenabled = true\ntrusted_hash = "sha256:aa"\n')
        self.init(agents=["codex"])
        initproj.remove_project(self.repo)
        self.assertEqual(list(self.tables()), ["/elsewhere/hooks.json:stop:0:0"])

    def test_a_table_the_user_changed_stays_and_is_reported(self):
        self.init(agents=["codex"])
        text = read(self.cfg)
        key = sorted(self.tables())[0]
        write(self.cfg, text.replace('[hooks.state."%s"]\nenabled = true' % key,
                                     '[hooks.state."%s"]\nuser_note = "mine"\nenabled = true' % key))
        lines = initproj.remove_project(self.repo)
        self.assertTrue(any("was changed since foreman wrote it" in l for l in lines), lines)
        self.assertIn(key, self.tables())
        self.assertEqual(len(self.tables()), 1)
        self.assertEqual(len(self.record()), 1)  # still recorded, to be retried

    def test_a_pending_codex_entry_is_reported_and_dropped_never_removed(self):
        write(self.cfg, '[projects."%s"]\ntrust_level = "trusted"\n' % self.repo)
        trust._put({"id": "tr-x", "file": os.path.realpath(self.cfg), "kind": "codex-project", "value": self.repo,
                    "values": {"trust_level": "trusted"}, "text": "", "project": "app", "scope": "project",
                    "worktree": None, "state": "pending", "at": "t"})
        lines = trust.remove_all()
        self.assertTrue(any("crash left it pending" in l for l in lines), lines)
        self.assertEqual(parsed(self.cfg)["projects"], {self.repo: {"trust_level": "trusted"}})
        self.assertEqual(self.record(), [])
