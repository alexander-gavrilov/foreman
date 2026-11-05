import json
import os
import unittest

from tests.helpers import ForemanTestCase, run_cli, BIN, ROOT

ALL_COMMANDS = ["status", "limits", "send", "ask", "reply", "say", "open", "close", "rotate", "board", "log",
                "stage", "watch", "clean", "work", "report", "init", "install", "uninstall", "web", "serve",
                "hook", "tap", "deliver", "gate", "telegram", "orchestrator"]
STUBS = [c for c in ALL_COMMANDS if c not in ("board", "log", "status", "gate", "telegram")]


class LauncherTest(ForemanTestCase):
    def test_launcher_is_executable(self):
        self.assertTrue(os.access(BIN, os.X_OK))

    def test_launcher_through_symlink(self):
        link = os.path.join(self.tmp, "bin-link")
        os.symlink(BIN, link)
        code, out, err = run_cli_path(link, "gate", "list", cwd=self.work)
        # not inside a project -> clean error, but the launcher itself must resolve ../lib
        self.assertNotIn("Traceback", err)
        self.assertNotIn("ModuleNotFoundError", err)

    def test_help_lists_every_command(self):
        code, out, err = run_cli("--help")
        self.assertEqual(code, 0)
        for c in ALL_COMMANDS:
            self.assertIn(c, out)

    def test_no_args_prints_help_and_exits_2(self):
        code, out, err = run_cli()
        self.assertEqual(code, 2)

    def test_all_command_modules_exist_with_contract(self):
        import importlib
        for c in ALL_COMMANDS:
            mod = importlib.import_module("foreman.commands." + c)
            self.assertTrue(callable(mod.register), c)
            self.assertTrue(callable(mod.run), c)

    def test_stubs(self):
        for c in STUBS:
            code, out, err = run_cli(c)
            self.assertEqual(code, 2, c)
            self.assertIn("foreman %s: not implemented yet" % c, out + err)

    def test_stubs_accept_arbitrary_arguments(self):
        code, out, err = run_cli("send", "s-123456", "hello", "--flag", "x")
        self.assertEqual(code, 2)
        self.assertIn("not implemented yet", out + err)


def run_cli_path(path, *args, cwd=None):
    import subprocess, sys
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    p = subprocess.run([sys.executable, path] + list(args), capture_output=True, text=True, env=env, cwd=cwd)
    return p.returncode, p.stdout, p.stderr


class ProjectResolutionTest(ForemanTestCase):
    def test_outside_project_errors(self):
        code, out, err = run_cli("board", "show", cwd=self.work)
        self.assertEqual(code, 2)
        self.assertIn("no foreman project", err)

    def test_unknown_project_slug(self):
        code, out, err = run_cli("board", "show", "--project", "ghost", cwd=self.work)
        self.assertEqual(code, 2)
        self.assertIn("ghost", err)

    def test_resolves_from_subfolder(self):
        self.init_project("demo")
        sub = self.make_dir("demo/a/b")
        code, out, err = run_cli("board", "show", cwd=sub)
        self.assertEqual(code, 0, err)
        self.assertIn("demo", out)


class BoardCliTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.init_project("demo")
        self.cwd = self.proj["path"]

    def cli(self, *a, **kw):
        return run_cli(*a, cwd=kw.pop("cwd", self.cwd), **kw)

    def test_empty_show(self):
        code, out, err = self.cli("board", "show")
        self.assertEqual(code, 0)
        self.assertIn("demo", out)
        self.assertIn("no tasks", out)

    def test_add_and_show(self):
        code, out, err = self.cli("board", "add", "Write parser", "--stage", "build", "--owner", "s-1a2b3c",
                                  "--next", "run tests", "--link", "pr=http://x/1", "--link", "spec=http://x/2")
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), "t1")
        code, out, err = self.cli("board", "show")
        self.assertEqual(code, 0)
        self.assertIn("t1", out)
        self.assertIn("Write parser", out)
        self.assertIn("build", out)
        self.assertIn("s-1a2b3c", out)
        self.assertIn("run tests", out)
        self.assertIn("pr=http://x/1", out)

    def test_show_json(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "show", "--json")
        data = json.loads(out)
        self.assertEqual(data["tasks"][0]["title"], "A")
        self.assertEqual(data["project"], "demo")

    def test_project_flag_from_elsewhere(self):
        self.cli("board", "add", "A")
        code, out, err = run_cli("board", "show", "--project", "demo", cwd=self.work)
        self.assertEqual(code, 0, err)
        self.assertIn("A", out)

    def test_bad_link(self):
        code, out, err = self.cli("board", "add", "A", "--link", "nolabel")
        self.assertEqual(code, 2)
        self.assertIn("label=url", err)

    def test_set(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "set", "t1", "stage=review", "next=merge it", "owner=s-2")
        self.assertEqual(code, 0, err)
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual((task["stage"], task["next"], task["owner"]), ("review", "merge it", "s-2"))

    def test_set_errors(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "set", "t9", "stage=x")
        self.assertEqual(code, 2)
        self.assertIn("t9", err)
        code, out, err = self.cli("board", "set", "t1", "bogus=x")
        self.assertEqual(code, 2)
        code, out, err = self.cli("board", "set", "t1", "noequals")
        self.assertEqual(code, 2)
        code, out, err = self.cli("board", "set", "t1", "status=done")
        self.assertEqual(code, 2)
        self.assertIn("done", err)

    def test_done_without_check(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "done", "t1")
        self.assertEqual(code, 0, err)
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual(task["status"], "done")

    def test_done_with_passing_check(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "done", "t1", "--check", "echo all green")
        self.assertEqual(code, 0, err)
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["check"]["exit_code"], 0)
        self.assertIn("all green", task["check"]["tail"])
        self.assertEqual(task["check"]["cmd"], "echo all green")

    def test_done_with_failing_check_keeps_task_open(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "done", "t1", "--check", "echo broken; echo bad >&2; exit 3")
        self.assertEqual(code, 1)
        self.assertIn("broken", out + err)
        self.assertIn("bad", out + err)
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["check"]["exit_code"], 3)
        self.assertIn("broken", task["check"]["tail"])

    def test_check_runs_in_project_folder(self):
        self.cli("board", "add", "A")
        with open(os.path.join(self.cwd, "marker.txt"), "w") as f:
            f.write("x")
        code, out, err = run_cli("board", "done", "t1", "--check", "test -f marker.txt",
                                 "--project", "demo", cwd=self.work)
        self.assertEqual(code, 0, err)

    def test_check_tail_is_bounded(self):
        self.cli("board", "add", "A")
        self.cli("board", "done", "t1", "--check", "seq 1 500; exit 1")
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        lines = task["check"]["tail"].splitlines()
        self.assertLessEqual(len(lines), 20)
        self.assertEqual(lines[-1], "500")

    def test_stored_check_is_used_by_done(self):
        self.cli("board", "add", "A")
        self.cli("board", "set", "t1", "check=exit 4")
        code, out, err = self.cli("board", "done", "t1")
        self.assertEqual(code, 1)
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["check"]["exit_code"], 4)

    def test_done_check_flag_cannot_replace_a_stored_check(self):
        self.cli("board", "add", "A")
        self.assertEqual(self.cli("board", "set", "t1", "check=false")[0], 0)
        code, out, err = self.cli("board", "done", "t1", "--check", "touch PWNED")
        self.assertEqual(code, 1)                               # the stored (failing) check ran
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "PWNED")))
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual(task["status"], "open")
        self.assertEqual(task["check"]["cmd"], "false")
        self.assertIn("ignored", err)

    def test_done_check_flag_may_set_a_check_when_none_is_stored(self):
        self.cli("board", "add", "A")
        code, out, err = self.cli("board", "done", "t1", "--check", "false")
        self.assertEqual(code, 1)
        task = json.loads(self.cli("board", "show", "--json")[1])["tasks"][0]
        self.assertEqual(task["check"]["cmd"], "false")
        # now the check is stored: a different --check no longer replaces it
        code, out, err = self.cli("board", "done", "t1", "--check", "true")
        self.assertEqual(code, 1)

    def test_same_check_flag_as_stored_is_fine(self):
        self.cli("board", "add", "A")
        self.cli("board", "set", "t1", "check=true")
        code, out, err = self.cli("board", "done", "t1", "--check", "true")
        self.assertEqual(code, 0, err)
        self.assertNotIn("ignored", err)

    def test_set_check_cannot_replace_a_stored_check(self):
        self.cli("board", "add", "A")
        self.assertEqual(self.cli("board", "set", "t1", "check=false")[0], 0)
        code, out, err = self.cli("board", "set", "t1", "check=true")
        self.assertEqual(code, 2)
        self.assertIn("check", err)
        self.assertEqual(self.cli("board", "done", "t1")[0], 1)

    def test_corrupt_board_is_reported_not_overwritten(self):
        self.cli("board", "add", "A")
        board = os.path.join(self.foreman_home, "projects/demo/board.json")
        with open(board, "w") as f:
            f.write("{broken")
        code, out, err = self.cli("board", "add", "B")
        self.assertEqual(code, 1)
        self.assertIn("corrupt", err)
        self.assertIn("board.json.corrupt-", err)
        self.assertFalse(os.path.exists(board))
        self.assertTrue([n for n in os.listdir(os.path.dirname(board)) if n.startswith("board.json.corrupt-")])
        self.assertNotIn("Traceback", err)


    def test_done_unknown_task(self):
        code, out, err = self.cli("board", "done", "t5")
        self.assertEqual(code, 2)


class AmbiguityCliTest(ForemanTestCase):
    def test_cliutil_find_session_scopes_to_caller_project(self):
        from foreman import sessions, cliutil
        a = self.init_project("alpha")
        b = self.init_project("beta")
        sa = sessions.create_session("alpha", "claude", title="api")
        sb = sessions.create_session("beta", "claude", title="api")
        os.chdir(a["path"])
        self.addCleanup(os.chdir, self.tmp)
        ns = type("A", (), {"project": None})()
        self.assertEqual(cliutil.find_session("api", ns)["sid"], sa["sid"])
        ns.project = "beta"
        self.assertEqual(cliutil.find_session("api", ns)["sid"], sb["sid"])
        os.chdir(self.work)
        ns.project = None
        with self.assertRaises(cliutil.CliError) as cm:
            cliutil.find_session("api", ns)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn(sa["sid"], cm.exception.message)
        self.assertIn(sb["sid"], cm.exception.message)
        with self.assertRaises(cliutil.CliError):
            cliutil.find_session("missing", ns)


class LogCliTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.proj = self.init_project("demo")
        self.cwd = self.proj["path"]

    def cli(self, *a, **kw):
        return run_cli(*a, cwd=self.cwd, **kw)

    def test_append_and_search(self):
        code, out, err = self.cli("log", "append", "chose sqlite for the cache", "--kind", "decision")
        self.assertEqual(code, 0, err)
        self.cli("log", "append", "deployed to staging")
        code, out, err = self.cli("log", "search", "sqlite")
        self.assertEqual(code, 0)
        self.assertIn("chose sqlite for the cache", out)
        self.assertIn("decision", out)
        self.assertNotIn("staging", out)

    def test_actor_from_env_and_default_kind(self):
        self.cli("log", "append", "x", env={"FOREMAN_ACTOR": "s-abcdef"})
        with open(os.path.join(self.foreman_home, "projects/demo/journal.jsonl")) as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["actor"], "s-abcdef")
        self.assertEqual(entry["kind"], "note")

    def test_search_regex_limit_and_no_match(self):
        for i in range(5):
            self.cli("log", "append", "step %d done" % i)
        out = self.cli("log", "search", r"step [0-9] done", "--limit", "2")[1]
        self.assertEqual(len(out.strip().splitlines()), 2)
        self.assertIn("step 4", out)
        code, out, err = self.cli("log", "search", "zzz")
        self.assertEqual(code, 1)  # grep-style: no match
        self.assertEqual(out.strip(), "")

    def test_search_json(self):
        self.cli("log", "append", "hello")
        out = self.cli("log", "search", "hell", "--json")[1]
        self.assertEqual(json.loads(out)[0]["text"], "hello")


class StatusCliTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import sessions
        self.s = sessions
        self.proj = self.init_project("demo")
        self.cwd = self.proj["path"]
        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:00Z"
        a = sessions.create_session("demo", "claude", model="sonnet", title="fm-api", owner="orchestrator",
                                    purpose="api")
        os.environ["FOREMAN_NOW"] = "2026-11-05T12:00:01Z"
        b = sessions.create_session("demo", "antigravity", model="flash", title="research", owner="user")
        sessions.update_session(a["sid"], context_tokens=152000, state="busy",
                                state_since="2026-11-05T11:56:00Z")
        sessions.update_session(b["sid"], state="idle", state_since="2026-11-05T09:00:00Z")
        self.a, self.b = a["sid"], b["sid"]
        # NOW for the CLI subprocess
        self.env = {"FOREMAN_NOW": "2026-11-05T12:00:00Z"}

    def cli(self, *a, **kw):
        return run_cli(*a, cwd=kw.pop("cwd", self.cwd), env=self.env, **kw)

    def test_lines(self):
        code, out, err = self.cli("status")
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("# demo"), lines[0])
        row_a = [l for l in lines if "fm-api" in l][0]
        for part in (self.a, "claude/sonnet", "busy 4m", "152k ⚠", "orchestrator"):
            self.assertIn(part, row_a)
        row_b = [l for l in lines if "research" in l][0]
        for part in (self.b, "antigravity/flash", "idle 3h", "unknown", "user"):
            self.assertIn(part, row_b)
        self.assertNotIn("⚠", row_b)

    def test_context_threshold_uses_config(self):
        self.write_config({"thresholds": {"context_soft": 200000}})
        out = self.cli("status")[1]
        row_a = [l for l in out.splitlines() if "fm-api" in l][0]
        self.assertIn("152k", row_a)
        self.assertNotIn("⚠", row_a)

    def test_closed_hidden_rotated_shown(self):
        self.s.set_state(self.b, "rotated")
        self.s.update_session(self.b, replaced_by="s-abcdef")
        c = self.s.create_session("demo", "claude", title="old-one")
        self.s.set_state(c["sid"], "closed")
        out = self.cli("status")[1]
        self.assertNotIn("old-one", out)
        row_b = [l for l in out.splitlines() if "research" in l][0]
        self.assertIn("rotated", row_b)
        self.assertIn("s-abcdef", row_b)

    def test_json(self):
        code, out, err = self.cli("status", "--json")
        data = json.loads(out)
        self.assertEqual(data["projects"][0]["slug"], "demo")
        self.assertEqual(len(data["projects"][0]["sessions"]), 2)

    def test_all_projects_and_project_flag(self):
        other = self.init_project("other")
        self.s.create_session("other", "codex", title="elsewhere")
        out = self.cli("status", "--all")[1]
        self.assertIn("# demo", out)
        self.assertIn("# other", out)
        out = run_cli("status", "--project", "other", cwd=self.work, env=self.env)[1]
        self.assertIn("elsewhere", out)
        self.assertNotIn("fm-api", out)

    def test_outside_project_shows_all(self):
        code, out, err = run_cli("status", cwd=self.work, env=self.env)
        self.assertEqual(code, 0, err)
        self.assertIn("# demo", out)

    def test_no_projects(self):
        import shutil
        shutil.rmtree(os.path.join(self.foreman_home, "projects"))
        code, out, err = run_cli("status", cwd=self.work, env=self.env)
        self.assertEqual(code, 0)
        self.assertIn("no projects", out)

    def test_project_without_sessions(self):
        self.init_project("empty")
        out = run_cli("status", "--project", "empty", cwd=self.work, env=self.env)[1]
        self.assertIn("no sessions", out)


class GateCliTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import projects
        self.proj = self.init_project("demo")
        self.proj["gates"] = ["deploy", "delete-data"]
        projects.save_project(self.proj)
        self.cwd = self.proj["path"]

    def test_list(self):
        code, out, err = run_cli("gate", "list", cwd=self.cwd)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.split(), ["deploy", "delete-data"])

    def test_list_json_and_empty(self):
        out = run_cli("gate", "list", "--json", cwd=self.cwd)[1]
        self.assertEqual(json.loads(out), ["deploy", "delete-data"])
        self.init_project("bare")
        out = run_cli("gate", "list", "--project", "bare", cwd=self.work)[1]
        self.assertIn("none", out)

    def test_check_configured_gate(self):
        code, out, err = run_cli("gate", "check", "deploy", cwd=self.cwd)
        self.assertEqual(code, 10)
        self.assertEqual(out.strip(), "ask the user")

    def test_check_unconfigured_gate(self):
        code, out, err = run_cli("gate", "check", "refactor", cwd=self.cwd)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "proceed")


if __name__ == "__main__":
    unittest.main()
