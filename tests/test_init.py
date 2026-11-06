import json
import os

from tests.helpers import ForemanTestCase, run_cli
from foreman import initproj, projects, sessions  # noqa: E402
from foreman import install as machine_install  # noqa: E402
from foreman.transports.fake import FakeTransport  # noqa: E402

FM = "/opt/foreman/bin/foreman"


def fmh():
    """The launcher as it appears at the start of a hook command (with FOREMAN_HOME=... when not the default)."""
    return machine_install.env_prefix() + FM


def jload(path):
    with open(path) as f:
        return json.load(f)


def tree(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != ".git"]
        for f in files:
            p = os.path.join(d, f)
            with open(p) as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


class InitTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.make_dir("app")
        os.makedirs(os.path.join(self.proj, ".git", "info"))

    def init(self, **kw):
        kw.setdefault("agents", ["claude", "antigravity", "codex"])
        kw.setdefault("transports", [])
        kw.setdefault("fm", FM)
        return initproj.init_project(self.proj, **kw)

    def test_first_run_creates_everything(self):
        changes, notes = self.init()
        p = projects.project_for_path(self.proj)
        self.assertEqual(p["slug"], "app")
        self.assertTrue(os.path.exists(os.path.join(self.foreman_home, "projects", "app", "board.json")))
        claude = jload(os.path.join(self.proj, ".claude", "settings.local.json"))["hooks"]
        self.assertEqual(sorted(claude), sorted(initproj.CLAUDE_EVENTS))
        self.assertEqual(claude["Stop"][0]["hooks"][0]["command"], fmh() + " hook --agent claude --event Stop")
        self.assertEqual(claude["PreToolUse"][0]["matcher"], "*")
        agy = jload(os.path.join(self.proj, ".agents", "hooks.json"))["foreman"]
        self.assertTrue(agy["enabled"])
        self.assertEqual(agy["Stop"], [{"type": "command", "timeout": 10,
                                        "command": fmh() + " hook --agent antigravity --event Stop"}])
        self.assertEqual(agy["PreToolUse"][0]["matcher"], "*")
        self.assertEqual(agy["PostToolUse"][0]["hooks"][0]["command"],
                         fmh() + " hook --agent antigravity --event PostToolUse")
        self.assertEqual(sorted(agy), sorted(["enabled", "SessionStart", "PreInvocation", "Stop", "PreToolUse",
                                              "PostToolUse"]))
        codex = jload(os.path.join(self.proj, ".codex", "hooks.json"))["hooks"]
        self.assertNotIn("Notification", codex)
        self.assertIn("PermissionRequest", codex)
        self.assertTrue(any("Codex" in n and "/hooks" in n for n in notes))
        self.assertTrue(any("Antigravity" in n and "trust" in n.lower() for n in notes))
        self.assertIn("no Telegram on this machine", notes)
        exclude = open(os.path.join(self.proj, ".git", "info", "exclude")).read()
        for rel in (".claude/settings.local.json", ".agents/hooks.json", ".codex/hooks.json"):
            self.assertIn("/" + rel, exclude)
        for name in ("AGENTS.md", "CLAUDE.md"):
            self.assertFalse(os.path.exists(os.path.join(self.proj, name)))
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".gitignore")))

    def test_second_run_changes_nothing(self):
        self.init()
        before = tree(self.proj)
        state = tree(self.foreman_home)
        exclude = open(os.path.join(self.proj, ".git", "info", "exclude")).read()
        changes, _ = self.init()
        self.assertEqual(changes, [])
        self.assertEqual(tree(self.proj), before)
        self.assertEqual({k: v for k, v in tree(self.foreman_home).items() if not k.endswith(".lock")},
                         {k: v for k, v in state.items() if not k.endswith(".lock")})
        self.assertEqual(open(os.path.join(self.proj, ".git", "info", "exclude")).read(), exclude)

    def test_cli_prints_already_correct(self):
        env = {"PATH": os.path.dirname(os.__file__)}  # no agent binaries on PATH
        os.makedirs(os.path.join(self.home, ".claude"))
        code, out, err = run_cli("init", "--path", self.proj, env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("registered project app", out)
        self.assertIn("wrote claude hooks", out)
        code, out, err = run_cli("init", "--path", self.proj, env=env)
        self.assertIn("already correct", out)
        self.assertNotIn("wrote", out)

    def test_existing_user_hooks_preserved(self):
        path = os.path.join(self.proj, ".claude", "settings.local.json")
        os.makedirs(os.path.dirname(path))
        mine = {"permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                          "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "lint.sh"}]}],
                          "Custom": [{"hooks": []}]}}
        with open(path, "w") as f:
            json.dump(mine, f)
        self.init(agents=["claude"])
        data = jload(path)
        self.assertEqual(data["permissions"], mine["permissions"])
        self.assertEqual(data["hooks"]["Custom"], [{"hooks": []}])
        stop = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(stop, ["echo mine", fmh() + " hook --agent claude --event Stop"])
        pre = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(pre, ["lint.sh", fmh() + " hook --agent claude --event PreToolUse"])
        before = open(path).read()
        self.init(agents=["claude"])
        self.assertEqual(open(path).read(), before)

    def test_stale_foreman_path_is_refreshed_not_duplicated(self):
        self.init(agents=["claude"], fm="/old/foreman")
        self.init(agents=["claude"])
        data = jload(os.path.join(self.proj, ".claude", "settings.local.json"))
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], fmh() + " hook --agent claude --event Stop")

    def test_invalid_json_is_not_clobbered(self):
        path = os.path.join(self.proj, ".claude", "settings.local.json")
        os.makedirs(os.path.dirname(path))
        open(path, "w").write("{not json")
        with self.assertRaises(initproj.InitError):
            self.init(agents=["claude"])
        self.assertEqual(open(path).read(), "{not json")

    def test_committed_files_never_edited(self):
        agents_md = os.path.join(self.proj, "AGENTS.md")
        claude_md = os.path.join(self.proj, "CLAUDE.md")
        open(agents_md, "w").write("# Mine\n")
        open(claude_md, "w").write("# Claude rules\n")
        self.init(agents=[])
        self.assertEqual(open(agents_md).read(), "# Mine\n")
        self.assertEqual(open(claude_md).read(), "# Claude rules\n")
        exclude = open(os.path.join(self.proj, ".git", "info", "exclude")).read()
        for rel in (".claude/settings.local.json", ".agents/hooks.json", ".codex/hooks.json"):
            self.assertIn("/" + rel, exclude)

    def test_agent_detection(self):
        self.assertEqual(initproj.detect_agents(self.home, which=lambda b: None), [])
        os.makedirs(os.path.join(self.home, ".codex"))
        self.assertEqual(initproj.detect_agents(self.home, which=lambda b: None), ["codex"])
        self.assertEqual(initproj.detect_agents(self.home, which=lambda b: "/x/claude" if b == "claude" else None),
                         ["claude", "codex"])

    def test_exclude_in_git_worktree(self):
        wt = self.make_dir("wt")
        common = os.path.join(self.tmp, "main.git")
        gitdir = os.path.join(common, "worktrees", "wt")
        os.makedirs(gitdir)
        open(os.path.join(gitdir, "commondir"), "w").write("../..\n")
        open(os.path.join(wt, ".git"), "w").write("gitdir: %s\n" % gitdir)
        initproj.init_project(wt, agents=["claude"], transports=[], fm=FM)
        self.assertIn("/.claude/settings.local.json", open(os.path.join(common, "info", "exclude")).read())

    def test_registers_open_sessions_in_project(self):
        fake = FakeTransport()
        inside = os.path.join(self.proj, "sub")
        os.makedirs(inside)
        for cwd, agent in ((self.proj, "claude"), (inside, "antigravity"), (self.make_dir("other"), "claude")):
            fake.launch(agent, None, None, cwd, "", "t-" + agent)
        changes, notes = self.init(transports=[fake])
        recs = sessions.list_sessions("app")
        self.assertEqual(sorted(r["agent"] for r in recs), ["antigravity", "claude"])
        self.assertTrue(all(r["owner"] == "user" and r["role"] == "worker" for r in recs))
        self.assertEqual(recs[0]["transport"]["kind"], "fake")
        self.assertTrue(any("foreman orchestrator adopt" in n for n in notes))
        changes, notes2 = self.init(transports=[fake])  # known handles are skipped
        self.assertEqual(changes, [])
        self.assertEqual(len(sessions.list_sessions("app")), 2)
        self.assertTrue(any("foreman orchestrator adopt" in n for n in notes2))

    def test_plain_shell_terminals_are_not_registered(self):  # fix round 7: found live
        fake = FakeTransport()
        fake.launch("claude", None, None, self.proj, "", "agent-tab")
        fake.launch(None, None, None, self.proj, "", "shell-tab")
        changes, notes = self.init(transports=[fake])
        recs = sessions.list_sessions("app")
        self.assertEqual([r["agent"] for r in recs], ["claude"])
        self.assertFalse(any("shell-tab" in c or "unknown" in c for c in changes))

    def test_only_shells_registers_nothing_and_suggests_no_adopt(self):
        fake = FakeTransport()
        fake.launch(None, None, None, self.proj, "", "shell-tab")
        changes, notes = self.init(transports=[fake])
        self.assertEqual(sessions.list_sessions("app"), [])
        self.assertFalse(any("foreman orchestrator adopt" in n for n in notes))

    def test_telegram_topic_saved(self):
        from foreman import telegram
        saved = telegram.create_topic
        telegram.create_topic = lambda name: 42
        self.addCleanup(setattr, telegram, "create_topic", saved)
        changes, notes = self.init(agents=[])
        self.assertIn("created the Telegram topic", changes)
        self.assertEqual(projects.project_for_path(self.proj)["telegram_topic_id"], 42)
        self.assertNotIn("no Telegram on this machine", notes)
        telegram.create_topic = lambda name: self.fail("must not create a second topic")
        self.assertEqual(self.init(agents=[])[0], [])

    def test_not_a_folder(self):
        with self.assertRaises(initproj.InitError):
            initproj.init_project(os.path.join(self.tmp, "nope"), agents=[], transports=[])


class InitRemoveTest(ForemanTestCase):
    """F3 (review 3c): `foreman init --remove` takes foreman out of a project again; uninstall runs it."""
    USER_HANDLER = {"type": "command", "command": "/usr/bin/my-linter --on-stop"}

    def setUp(self):
        super().setUp()
        self.proj = self.make_dir("app")
        os.makedirs(os.path.join(self.proj, ".git", "info"))
        self.exclude = os.path.join(self.proj, ".git", "info", "exclude")
        with open(self.exclude, "w") as f:
            f.write("# mine\n/build/\n")

    def init(self):
        return initproj.init_project(self.proj, agents=["claude", "antigravity", "codex"], transports=[], fm=FM)

    def put_user_hook(self):
        path = os.path.join(self.proj, ".claude", "settings.local.json")
        data = jload(path)
        data["permissions"] = {"allow": ["Bash(ls)"]}
        data["hooks"]["Stop"].append({"hooks": [self.USER_HANDLER]})
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_remove_deletes_foreman_only_files_and_exclude_lines(self):
        self.init()
        changes = initproj.remove_project(self.proj)
        for rel in (".claude/settings.local.json", ".agents/hooks.json", ".codex/hooks.json"):
            self.assertFalse(os.path.exists(os.path.join(self.proj, rel)), rel)
        self.assertEqual(open(self.exclude).read(), "# mine\n/build/\n")
        self.assertTrue(changes)
        self.assertEqual(initproj.remove_project(self.proj), [])  # idempotent
        # the project record and its state stay
        self.assertIsNotNone(projects.project_for_path(self.proj))

    def test_remove_keeps_the_users_own_handlers_and_settings(self):
        self.init()
        path = self.put_user_hook()
        agy = os.path.join(self.proj, ".agents", "hooks.json")
        data = jload(agy)
        data["mine"] = {"enabled": True}
        with open(agy, "w") as f:
            json.dump(data, f)
        initproj.remove_project(self.proj)
        left = jload(path)
        self.assertEqual(left, {"permissions": {"allow": ["Bash(ls)"]},
                                "hooks": {"Stop": [{"hooks": [self.USER_HANDLER]}]}})
        self.assertEqual(jload(agy), {"mine": {"enabled": True}})
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".codex", "hooks.json")))

    def test_remove_does_not_touch_a_project_that_was_never_initialised(self):
        self.assertEqual(initproj.remove_project(self.proj), [])
        self.assertEqual(open(self.exclude).read(), "# mine\n/build/\n")

    def test_cli_init_remove(self):
        self.init()
        code, out, err = run_cli("init", "--remove", "--path", self.proj)
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".claude", "settings.local.json")))
        code, out, err = run_cli("init", "--remove", "--path", self.proj)
        self.assertEqual(code, 0)
        self.assertIn("nothing to remove", out)

    def test_uninstall_removes_hooks_of_every_registered_project(self):
        from foreman import install
        self.init()
        other = self.make_dir("other")
        os.makedirs(os.path.join(other, ".git", "info"))
        initproj.init_project(other, agents=["claude"], transports=[], fm=FM)
        gone = self.make_dir("gone")
        initproj.init_project(gone, agents=["claude"], transports=[], fm=FM)
        import shutil
        shutil.rmtree(gone)  # a registered project whose folder vanished must not break uninstall
        changes = install.uninstall(platform="linux", runner=lambda argv: 0)
        for root in (self.proj, other):
            self.assertFalse(os.path.exists(os.path.join(root, ".claude", "settings.local.json")), root)
        self.assertTrue(any("hooks" in c and "app" in c for c in changes), changes)


def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t"] + list(args), cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


class _GitSubfolderBase(ForemanTestCase):
    def setUp(self):
        super().setUp()
        import shutil
        if not shutil.which("git"):
            self.skipTest("git not available")
        self.repo = self.make_dir("mono")
        _git(self.repo, "init", "-q")
        self.sub = os.path.join(self.repo, "services", "billing")
        os.makedirs(self.sub)
        with open(os.path.join(self.sub, "main.py"), "w") as f:
            f.write("x = 1\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")


class InitInGitSubfolder(_GitSubfolderBase):
    """F6 (review 3c): init in a repo subfolder must keep the working tree clean too."""

    def test_subfolder_init_leaves_the_tree_clean_and_remove_restores_exclude(self):
        before = open(os.path.join(self.repo, ".git", "info", "exclude")).read()
        initproj.init_project(self.sub, agents=["claude", "antigravity", "codex"], transports=[], fm=FM)
        self.assertTrue(os.path.exists(os.path.join(self.sub, ".claude", "settings.local.json")))
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        exclude = open(os.path.join(self.repo, ".git", "info", "exclude")).read()
        self.assertIn("/services/billing/.claude/settings.local.json", exclude)
        # idempotent
        changes, _ = initproj.init_project(self.sub, agents=["claude", "antigravity", "codex"], transports=[], fm=FM)
        self.assertFalse([c for c in changes if "excluded" in c], changes)
        initproj.remove_project(self.sub)
        self.assertEqual(open(os.path.join(self.repo, ".git", "info", "exclude")).read(), before)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")

    def test_repo_root_init_still_excludes_at_the_root(self):
        initproj.init_project(self.repo, agents=["claude"], transports=[], fm=FM)
        self.assertIn("/.claude/settings.local.json", open(os.path.join(self.repo, ".git", "info", "exclude")).read())
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")


class InitSkipsTrackedHookFiles(_GitSubfolderBase):
    """F7 (review 3c): a hook file that is committed in the repo is never edited (D41: the tree stays clean)."""

    def test_tracked_hook_file_is_left_alone_with_a_note(self):
        codex = os.path.join(self.sub, ".codex", "hooks.json")
        os.makedirs(os.path.dirname(codex))
        with open(codex, "w") as f:
            f.write('{"hooks": {}}\n')
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "codex hooks")
        changes, notes = initproj.init_project(self.sub, agents=["claude", "codex"], transports=[], fm=FM)
        self.assertEqual(open(codex).read(), '{"hooks": {}}\n')
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertTrue(any(".codex/hooks.json" in n and "tracked" in n for n in notes), notes)
        self.assertFalse([c for c in changes if "codex" in c], changes)
        self.assertNotIn("codex/hooks.json", open(os.path.join(self.repo, ".git", "info", "exclude")).read())
        # the untracked claude file was still written and excluded
        self.assertTrue(os.path.exists(os.path.join(self.sub, ".claude", "settings.local.json")))


class HookCommandsCarryForemanHome(ForemanTestCase):
    """F11 (review 3c): with a non-default FOREMAN_HOME, the hook commands carry it, so hooks and the daemon
    share one state."""

    def init(self, name):
        proj = self.make_dir(name)
        initproj.init_project(proj, agents=["claude", "antigravity", "codex"], transports=[], fm=FM)
        return proj

    def commands(self, proj):
        out = []
        for rel in (".claude/settings.local.json", ".codex/hooks.json"):
            for groups in jload(os.path.join(proj, rel))["hooks"].values():
                out += [h["command"] for g in groups for h in g["hooks"]]
        agy = jload(os.path.join(proj, ".agents", "hooks.json"))["foreman"]
        for ev, v in agy.items():
            if isinstance(v, list):
                out += [h["command"] if "command" in h else h["hooks"][0]["command"] for h in v]
        return out

    def test_custom_foreman_home_is_in_every_hook_command(self):
        import shlex
        proj = self.init("custom")
        prefix = "FOREMAN_HOME=%s " % shlex.quote(self.foreman_home)
        cmds = self.commands(proj)
        self.assertGreater(len(cmds), 15)
        for c in cmds:
            self.assertTrue(c.startswith(prefix + FM + " hook --agent "), c)

    def test_default_foreman_home_adds_nothing(self):
        os.environ.pop("FOREMAN_HOME")
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "xdg")  # default location, under the temp dir
        proj = self.init("plain")
        for c in self.commands(proj):
            self.assertTrue(c.startswith(FM + " hook --agent "), c)
        # and explicitly naming the default is the same thing
        os.environ["FOREMAN_HOME"] = os.path.join(self.tmp, "xdg", "foreman")
        proj = self.init("plain2")
        for c in self.commands(proj):
            self.assertTrue(c.startswith(FM + " hook --agent "), c)

    def test_refresh_after_changing_foreman_home_rewrites_the_commands(self):
        proj = self.init("moved")
        os.environ["FOREMAN_HOME"] = os.path.join(self.tmp, "other-state", "foreman")
        initproj.init_project(proj, agents=["claude"], transports=[], fm=FM)
        for c in jload(os.path.join(proj, ".claude", "settings.local.json"))["hooks"]["Stop"][0]["hooks"]:
            self.assertIn("other-state", c["command"])
            self.assertNotIn(self.foreman_home, c["command"])
