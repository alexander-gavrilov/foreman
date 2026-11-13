"""Spec §8.13 and §8.14 — foreman writes the narrow agent trust itself, and refuses where it must. Through the real entry
point (`skills/foreman/bin/foreman` as a subprocess) in a temporary HOME whose state folder is the default one for it,
with a stub `codex app-server` (tests/stubs/codex) and the file-backed fake transport. The real ~/.gemini and ~/.codex
are never touched: every agent file lives under the temporary HOME.

What this cannot show (needs a live agent): that Antigravity really loads the hooks of the trusted folder and worktree
(§10.4), and that a Codex hook fires in a worktree of a logged-in session (§10.6); Codex's status comes from `hooks/list`."""
import json
import os

from tests.helpers import run_cli
from tests.trustbase import TrustCase, git, read, readb, write
from foreman import sessions  # noqa: E402

ORIGINAL_AGY = '{\n  "theme": "dark",\n  "trustedWorkspaces": [\n    "/home/x/projects"\n  ]\n}\n'
ORIGINAL_CFG = '# my codex\nmodel = "o3"\n'


class TrustAcceptance(TrustCase):
    def setUp(self):
        super().setUp()
        self.env = {"FOREMAN_INSTALL_DRY_SERVICES": "1", "FOREMAN_TRANSPORT": "fake"}
        write(self.agy, ORIGINAL_AGY)
        write(self.cfg, ORIGINAL_CFG)

    def cli(self, *args, **kw):
        return run_cli(*args, env=dict(self.env, **kw.pop("env", {})), cwd=kw.pop("cwd", self.repo), **kw)

    def codex_status(self, folder):
        from foreman import install, trust
        mine, _ = trust.real_hooks(folder, install.foreman_bin())
        return {h["trustStatus"] for h in mine.values()}, len(mine)

    def test_13_init_trusts_the_folder_and_the_worktree_and_uninstall_leaves_no_trace(self):
        code, out, err = self.cli("init")
        self.assertEqual(code, 0, out + err)
        self.assertIn(self.repo, json.loads(read(self.agy))["trustedWorkspaces"])
        self.assertEqual(self.codex_status(self.repo), ({"trusted"}, 7))
        # an Antigravity worker opened in a worktree: trusted before it starts
        code, out, err = self.cli("open", "--agent", "antigravity", "--purpose", "p", "--title", "agy-w",
                                  "--prompt", "go", "--transport", "fake")
        self.assertEqual(code, 0, out + err)
        (agy_rec,) = [r for r in sessions.list_sessions() if r["agent"] == "antigravity"]
        wt = agy_rec["worktree"]["path"]
        self.assertIn(wt, json.loads(read(self.agy))["trustedWorkspaces"])
        self.assertTrue(os.path.exists(os.path.join(wt, ".agents", "hooks.json")))
        # a Codex worker in a worktree: Codex reports the hooks trusted there, with no entry of its own
        code, out, err = self.cli("open", "--agent", "codex", "--purpose", "p", "--title", "codex-w", "--prompt", "go",
                                  "--transport", "fake")
        self.assertEqual(code, 0, out + err)
        (codex_rec,) = [r for r in sessions.list_sessions() if r["agent"] == "codex"]
        self.assertEqual(self.codex_status(codex_rec["worktree"]["path"]), ({"trusted"}, 7))
        self.assertNotIn(codex_rec["worktree"]["path"], read(self.cfg))
        # uninstall: every entry that was there before, none that foreman added
        code, out, err = self.cli("uninstall")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(read(self.agy), ORIGINAL_AGY)
        self.assertEqual(read(self.cfg), ORIGINAL_CFG)
        self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))

    def refused(self, *args, **kw):
        before = (readb(self.agy), readb(self.cfg))
        code, out, err = self.cli("init", *args, **kw)
        self.assertEqual(code, 1, out + err)
        self.assertEqual((readb(self.agy), readb(self.cfg)), before)
        self.assertFalse(os.path.exists(self.agy + ".foreman-bak"))
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))
        return out + err

    def test_14_the_home_folder_and_a_folder_of_repositories_are_refused(self):
        self.assertIn("home folder", self.refused("--path", self.home))
        self.assertIn("folder of repositories", self.refused("--path", self.work))

    def test_14_a_codex_folder_with_its_own_config_is_refused_and_the_files_are_unchanged(self):
        write(os.path.join(self.repo, ".codex", "config.toml"), 'approval_policy = "never"\n')
        cfg_before = readb(self.cfg)
        code, out, err = self.cli("init")
        self.assertEqual(code, 1, out + err)
        self.assertIn("config.toml", out)
        self.assertIn("your decision", out)
        self.assertEqual(readb(self.cfg), cfg_before)
        self.assertFalse(os.path.exists(self.cfg + ".foreman-bak"))

    def test_14_foreign_content_committed_after_init_is_refused_also_when_the_entry_predates_foreman(self):
        write(self.agy, json.dumps({"trustedWorkspaces": [self.repo]}))  # the user's own entry predates foreman
        code, out, err = self.cli("init")
        self.assertEqual(code, 0, out + err)
        write(os.path.join(self.repo, ".agents", "hooks.json"), json.dumps(dict(
            json.loads(read(os.path.join(self.repo, ".agents", "hooks.json"))),
            theirs={"enabled": True, "Stop": [{"type": "command", "command": "/usr/bin/evil"}]})))
        git(self.repo, "add", "-f", ".agents/hooks.json")
        git(self.repo, "commit", "-q", "-m", "a hook arrives after init")
        code, out, err = self.cli("open", "--agent", "antigravity", "--purpose", "p", "--title", "w", "--prompt", "go",
                                  "--transport", "fake")
        self.assertEqual(code, 1, out + err)
        self.assertIn("does not trust a folder", err)

    def test_14_an_orchestrators_session_cannot_make_foreman_trust_a_folder(self):
        from foreman import projects
        projects.create_project(self.repo)
        slug = projects.project_for_path(self.repo)["slug"]
        rec = sessions.create_session(project=slug, agent="claude", role="orchestrator", owner="orchestrator",
                                      title="orch", transport={"kind": "fake", "handle": "h"}, cwd=self.repo,
                                      state="idle", lifecycle="active")
        other = self.make_dir("other")
        git(other, "init", "-q")
        for args in ((), ("--path", other)):
            before = (readb(self.agy), readb(self.cfg))
            code, out, err = self.cli("init", *args, env={"FOREMAN_SESSION": rec["sid"]})
            self.assertEqual(code, 1, out + err)
            self.assertIn("may not trigger", err)
            self.assertEqual((readb(self.agy), readb(self.cfg)), before)

    def test_14_a_malformed_settings_file_is_left_alone_and_init_does_the_rest(self):
        write(self.agy, "{broken")
        code, out, err = self.cli("init")
        self.assertEqual(code, 1, out + err)
        self.assertIn("trust refused", out)
        self.assertEqual(read(self.agy), "{broken")
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".agents", "hooks.json")))  # init did everything else

    def test_14_an_entry_that_was_already_there_is_never_removed_even_after_a_crash_between_record_and_edit(self):
        from foreman import trust
        write(self.agy, json.dumps({"trustedWorkspaces": [self.repo]}))
        code, out, err = self.cli("init", "--path", self.repo)
        self.assertEqual(code, 0, out + err)
        trust._put({"id": "tr-crash", "file": os.path.realpath(self.agy), "kind": "agy-workspace", "value": self.repo,
                    "project": "app", "scope": "project", "worktree": None, "state": "pending", "at": "t"})
        code, out, err = self.cli("uninstall")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(json.loads(read(self.agy))["trustedWorkspaces"], [self.repo])

    def test_14_waiver_cases(self):
        """The user trusted the folder by hand and the folder holds content: init shows it and writes nothing; the same
        content opens, changed content is refused, so is an open after the user's entry was removed."""
        from foreman import initproj, install
        write(os.path.join(self.repo, ".agents", "hooks.json"), json.dumps({
            "foreman": initproj.antigravity_hooks(install.foreman_bin()),
            "theirs": {"enabled": True, "Stop": [{"type": "command", "command": "/usr/bin/theirs"}]}}))
        git(self.repo, "add", "-f", ".agents/hooks.json")
        git(self.repo, "commit", "-q", "-m", "hooks")
        write(self.agy, json.dumps({"trustedWorkspaces": [self.repo]}))
        code, out, err = self.cli("init")
        self.assertIn("already trusted by you", out)
        self.assertEqual(json.loads(read(self.agy))["trustedWorkspaces"], [self.repo])  # nothing written
        args = ("open", "--agent", "antigravity", "--purpose", "p", "--prompt", "go", "--transport", "fake")
        code, out, err = self.cli(*args, "--title", "same")
        self.assertEqual(code, 0, out + err)
        write(os.path.join(self.repo, ".agents", "hooks.json"), json.dumps({
            "foreman": initproj.antigravity_hooks(install.foreman_bin()),
            "theirs": {"enabled": True, "Stop": [{"type": "command", "command": "/usr/bin/changed"}]}}))
        git(self.repo, "add", "-f", ".agents/hooks.json")
        git(self.repo, "commit", "-q", "-m", "changed")
        code, out, err = self.cli(*args, "--title", "changed")
        self.assertEqual(code, 1, out + err)
        self.assertIn("new or changed", err)
        git(self.repo, "reset", "-q", "--hard", "HEAD~1")
        write(self.agy, json.dumps({"trustedWorkspaces": []}))
        code, out, err = self.cli(*args, "--title", "gone")
        self.assertEqual(code, 1, out + err)
        self.assertIn("your own trust entry", err)
