import json
import os

from tests.helpers import ForemanTestCase, run_cli
from foreman import initproj, projects, sessions  # noqa: E402
from foreman.transports.fake import FakeTransport  # noqa: E402

FM = "/opt/foreman/bin/foreman"


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
        self.assertEqual(claude["Stop"][0]["hooks"][0]["command"], FM + " hook --agent claude --event Stop")
        self.assertEqual(claude["PreToolUse"][0]["matcher"], "*")
        agy = jload(os.path.join(self.proj, ".agents", "hooks.json"))["foreman"]
        self.assertTrue(agy["enabled"])
        self.assertEqual(agy["Stop"], [{"type": "command", "timeout": 10,
                                        "command": FM + " hook --agent antigravity --event Stop"}])
        self.assertEqual(agy["PreToolUse"][0]["matcher"], "*")
        self.assertEqual(agy["PostToolUse"][0]["hooks"][0]["command"],
                         FM + " hook --agent antigravity --event PostToolUse")
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
        self.assertEqual(stop, ["echo mine", FM + " hook --agent claude --event Stop"])
        pre = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        self.assertEqual(pre, ["lint.sh", FM + " hook --agent claude --event PreToolUse"])
        before = open(path).read()
        self.init(agents=["claude"])
        self.assertEqual(open(path).read(), before)

    def test_stale_foreman_path_is_refreshed_not_duplicated(self):
        self.init(agents=["claude"], fm="/old/foreman")
        self.init(agents=["claude"])
        data = jload(os.path.join(self.proj, ".claude", "settings.local.json"))
        self.assertEqual(len(data["hooks"]["Stop"]), 1)
        self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], FM + " hook --agent claude --event Stop")

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
