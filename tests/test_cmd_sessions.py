"""open / close / rotate / orchestrator, against the fake transport (never a real terminal)."""
import contextlib
import io
import json
import os
import unittest
from unittest import mock

from tests.helpers import ForemanTestCase

OK = {"claude": "ok", "antigravity": "ok"}


def run_main(*args):
    from foreman import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class SessionTestCase(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import transports
        self.fake = transports.get_transport("fake")
        self.project = self.init_project("demo")
        self.slug = self.project["slug"]
        patcher = mock.patch("foreman.limits.levels", return_value=dict(OK))
        self.levels = patcher.start()
        self.addCleanup(patcher.stop)

    def sessions(self):
        from foreman import sessions
        return sessions.list_sessions(self.slug)

    def journal(self):
        from foreman import store, paths
        return store.read_jsonl(paths.journal_file(self.slug))

    def new_session(self, **kw):
        """A session record with a live fake terminal, as `open` would have left it."""
        from foreman import sessions
        handle = self.fake.launch(kw.get("agent", "claude"), kw.get("model", "sonnet"), None,
                                  self.project["path"], "first prompt", kw.get("title", "fm-api"))
        fields = dict(project=self.slug, agent="claude", model="sonnet", role="worker", owner="orchestrator",
                      purpose="build the api", title="fm-api", transport={"kind": "fake", "handle": handle},
                      cwd=self.project["path"], state="idle", lifecycle="active")
        fields.update(kw)
        return sessions.create_session(**fields)


class OpenTest(SessionTestCase):
    def open(self, *extra):
        return run_main("open", "--project", self.slug, "--purpose", "build the api", "--title", "fm-api",
                        "--transport", "fake", *extra)

    def test_open_registers_a_pending_session_and_launches(self):
        code, out, err = self.open("--prompt", "do the thing")
        self.assertEqual(code, 0, err)
        (rec,) = self.sessions()
        self.assertIn(rec["sid"], out)
        self.assertIn("fm-api", out)
        self.assertIn("claude/sonnet", out)
        self.assertEqual((rec["state"], rec["lifecycle"], rec["role"], rec["owner"], rec["agent"], rec["model"]),
                         ("starting", "active", "worker", "orchestrator", "claude", "sonnet"))
        self.assertEqual((rec["purpose"], rec["title"], rec["cwd"]), ("build the api", "fm-api",
                                                                      self.project["path"]))
        self.assertEqual(rec["transport"]["kind"], "fake")
        self.assertIsNone(rec["agent_session_id"])
        h = rec["transport"]["handle"]
        self.assertEqual(self.fake.inputs(h), ["do the thing"])
        (term,) = self.fake.list()
        self.assertEqual((term.handle, term.title, term.agent, term.cwd),
                         (h, "fm-api", "claude", self.project["path"]))
        from foreman import sessions
        self.assertEqual(sessions.find_by_handle(h)["sid"], rec["sid"])  # the first hook binds by handle

    def test_journal_gets_one_line(self):
        self.open("--prompt", "p")
        (row,) = self.journal()
        self.assertEqual(row["kind"], "open")
        self.assertIn("fm-api", row["text"])
        self.assertIn("claude/sonnet", row["text"])
        self.assertEqual(row["data"]["sid"], self.sessions()[0]["sid"])

    def test_prompt_file(self):
        path = os.path.join(self.tmp, "brief.md")
        with open(path, "w") as f:
            f.write("long brief\nline two\n")
        code, out, err = self.open("--prompt-file", path)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.fake.inputs(self.sessions()[0]["transport"]["handle"]), ["long brief\nline two"])

    def test_prompt_is_required_and_exclusive(self):
        code, out, err = self.open()
        self.assertEqual(code, 2)
        self.assertIn("prompt", err)
        code, out, err = self.open("--prompt", "a", "--prompt-file", "b")
        self.assertEqual(code, 2)
        self.assertEqual(self.sessions(), [])

    def test_missing_prompt_file(self):
        code, out, err = self.open("--prompt-file", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 2)
        self.assertEqual(self.sessions(), [])

    def test_unknown_project(self):
        code, out, err = run_main("open", "--project", "nope", "--purpose", "x", "--prompt", "p",
                                  "--transport", "fake")
        self.assertEqual(code, 2)

    def test_kind_routes_the_model(self):
        code, out, err = self.open("--kind", "review", "--prompt", "p")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sessions()[0]["model"], "opus")

    def test_explicit_agent_model_effort(self):
        code, out, err = self.open("--agent", "antigravity", "--model", "gemini-4-argon-medium", "--effort", "high",
                                   "--prompt", "p")
        self.assertEqual(code, 0, err)
        rec = self.sessions()[0]
        self.assertEqual((rec["agent"], rec["model"], rec["effort"]), ("antigravity", "gemini-4-argon-medium", "high"))

    def test_model_without_agent_is_an_error(self):
        code, out, err = self.open("--model", "opus", "--prompt", "p")
        self.assertEqual(code, 2)
        self.assertEqual(self.sessions(), [])

    def test_warn_steers_to_the_other_provider_and_prints_the_note(self):
        self.levels.return_value = {"claude": "warn", "antigravity": "ok"}
        code, out, err = self.open("--prompt", "p")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sessions()[0]["agent"], "antigravity")
        self.assertIn("warn", out)
        self.assertIn("antigravity", out)

    def test_unknown_limits_are_noted(self):
        self.levels.return_value = {"claude": "unknown", "antigravity": "unknown"}
        code, out, err = self.open("--prompt", "p")
        self.assertEqual(code, 0, err)
        self.assertIn("unknown", out)

    def test_stop_is_refused_even_when_named_explicitly(self):
        self.levels.return_value = {"claude": "stop", "antigravity": "ok"}
        code, out, err = self.open("--agent", "claude", "--prompt", "p")
        self.assertEqual(code, 5)
        self.assertIn("claude", err)
        self.assertEqual(self.sessions(), [])
        self.assertEqual(self.fake.list(), [])
        code, out, err = self.open("--agent", "claude", "--model", "opus", "--prompt", "p")
        self.assertEqual(code, 5)

    def test_stop_everywhere_is_refused(self):
        self.levels.return_value = {"claude": "stop", "antigravity": "stop"}
        code, out, err = self.open("--prompt", "p")
        self.assertEqual(code, 5)
        self.assertEqual(self.fake.list(), [])

    def test_stop_reroutes_when_not_named(self):
        self.levels.return_value = {"claude": "stop", "antigravity": "ok"}
        code, out, err = self.open("--prompt", "p")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sessions()[0]["agent"], "antigravity")

    def test_owner_and_role(self):
        code, out, err = self.open("--owner", "user", "--role", "orchestrator", "--prompt", "p")
        self.assertEqual(code, 0, err)
        rec = self.sessions()[0]
        self.assertEqual((rec["owner"], rec["role"]), ("user", "orchestrator"))
        from foreman import projects
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], rec["sid"])

    def test_orchestrator_role_needs_a_capable_agent(self):
        code, out, err = self.open("--role", "orchestrator", "--agent", "codex", "--prompt", "p")
        self.assertEqual(code, 2)
        self.assertEqual(self.sessions(), [])

    def test_without_title_the_sid_is_the_title(self):
        code, out, err = run_main("open", "--project", self.slug, "--purpose", "x", "--prompt", "p",
                                  "--transport", "fake")
        self.assertEqual(code, 0, err)
        rec = self.sessions()[0]
        self.assertEqual(rec["title"], rec["sid"])
        self.assertEqual(self.fake.list()[0].title, rec["sid"])

    def test_plain_shell_handle_is_refused_in_registration_and_rename(self):
        from foreman.transports.fake import FakeTransport
        orig = FakeTransport.launch

        def launch_shell(ft, agent, model, effort, cwd, prompt, title):
            h = orig(ft, None, model, effort, cwd, prompt, None)
            return h

        with mock.patch.object(FakeTransport, "launch", launch_shell):
            code, out, err = self.open("--prompt", "p")
        self.assertEqual(code, 2)
        self.assertIn("shell", err.lower())
        self.assertEqual(self.sessions(), [])

    def test_launch_failure_leaves_no_record(self):
        with mock.patch.dict(os.environ, {"PATH": "/nonexistent"}):
            os.environ.pop("ORCA_CLI_COMMAND", None)
            code, out, err = run_main("open", "--project", self.slug, "--purpose", "x", "--prompt", "p",
                                      "--transport", "herdr")
        self.assertEqual(code, 1)
        self.assertIn("herdr", err)
        self.assertEqual(self.sessions(), [])

    def test_unknown_transport(self):
        code, out, err = run_main("open", "--project", self.slug, "--purpose", "x", "--prompt", "p",
                                  "--transport", "tmux")
        self.assertEqual(code, 2)

    def test_default_transport_selection(self):
        from foreman.commands import open as open_cmd
        pick = open_cmd.default_transport
        self.assertEqual(pick({"ORCA_TERMINAL_HANDLE": "term_x"}, lambda n: None), "orca")
        self.assertEqual(pick({"HERDR_PANE_ID": "w:p1"}, lambda n: None), "herdr")
        self.assertEqual(pick({"FOREMAN_TRANSPORT": "herdr", "ORCA_TERMINAL_HANDLE": "x"}, lambda n: None), "herdr")
        self.assertEqual(pick({}, lambda n: "/bin/" + n if n == "herdr" else None), "herdr")
        self.assertEqual(pick({}, lambda n: "/bin/" + n), "orca")
        self.assertEqual(pick({"ORCA_CLI_COMMAND": "/x/orca"}, lambda n: None), "orca")
        with self.assertRaises(Exception):
            pick({}, lambda n: None)

    def test_json_output(self):
        code, out, err = self.open("--prompt", "p", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["sid"], self.sessions()[0]["sid"])
        self.assertEqual(data["title"], "fm-api")


class CloseTest(SessionTestCase):
    def test_close_idle_session(self):
        rec = self.new_session()
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 0, err)
        self.assertIn(rec["sid"], out)
        from foreman import sessions
        now = sessions.load_session(rec["sid"])
        self.assertEqual(now["lifecycle"], "closed")
        self.assertEqual(now["state"], "idle")  # never put closed into state
        self.assertTrue(now["closed_at"])
        self.assertFalse(self.fake.alive(rec["transport"]["handle"]))
        (row,) = self.journal()
        self.assertEqual(row["kind"], "close")

    def test_close_by_title(self):
        rec = self.new_session()
        self.assertEqual(run_main("close", "fm-api")[0], 0)

    def test_busy_is_refused(self):
        rec = self.new_session(state="busy")
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 6)
        self.assertIn("busy", err)
        self.assertTrue(self.fake.alive(rec["transport"]["handle"]))
        from foreman import sessions
        self.assertEqual(sessions.load_session(rec["sid"])["state"], "busy")
        self.assertEqual(sessions.load_session(rec["sid"])["lifecycle"], "active")

    def test_user_owned_needs_consent(self):
        rec = self.new_session(owner="user")
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 7)
        self.assertTrue(self.fake.alive(rec["transport"]["handle"]))
        code, out, err = run_main("close", rec["sid"], "--user-consent")
        self.assertEqual(code, 0, err)
        self.assertFalse(self.fake.alive(rec["transport"]["handle"]))

    def test_pending_inbox_is_refused(self):
        from foreman import sessions
        rec = self.new_session()
        sessions.inbox_enqueue(rec["sid"], "do this first")
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 8)
        self.assertIn("pending", err)
        self.assertTrue(self.fake.alive(rec["transport"]["handle"]))
        sessions.inbox_mark_delivered(rec["sid"])
        self.assertEqual(run_main("close", rec["sid"])[0], 0)

    def test_order_busy_before_owner(self):
        rec = self.new_session(state="busy", owner="user")
        self.assertEqual(run_main("close", rec["sid"])[0], 6)

    def test_terminal_already_gone_still_closes(self):
        rec = self.new_session()
        self.fake.kill(rec["transport"]["handle"])
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 0, err)
        from foreman import sessions
        now = sessions.load_session(rec["sid"])
        self.assertEqual(now["lifecycle"], "closed")
        self.assertEqual(now["state"], "idle")

    def test_already_closed_is_idempotent(self):
        rec = self.new_session()
        run_main("close", rec["sid"])
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 0)
        self.assertIn("already closed", out)
        self.assertEqual(len(self.journal()), 1)

    def test_rotated_session_can_be_closed(self):
        rec = self.new_session(lifecycle="rotated")
        self.assertEqual(run_main("close", rec["sid"])[0], 0)
        from foreman import sessions
        self.assertEqual(sessions.load_session(rec["sid"])["lifecycle"], "closed")

    def test_unknown_session(self):
        self.assertEqual(run_main("close", "s-nope00")[0], 2)

    def test_transport_that_cannot_close(self):
        rec = self.new_session(transport={"kind": "claude-msg", "handle": "claude:abc"})
        code, out, err = run_main("close", rec["sid"])
        self.assertEqual(code, 9)
        from foreman import sessions
        self.assertEqual(sessions.load_session(rec["sid"])["state"], "idle")
        self.assertEqual(sessions.load_session(rec["sid"])["lifecycle"], "active")

    def test_closing_the_orchestrator_clears_the_project_pointer(self):
        from foreman import projects
        rec = self.new_session(role="orchestrator")
        proj = projects.load_project(self.slug)
        proj["orchestrator"] = rec["sid"]
        projects.save_project(proj)
        self.assertEqual(run_main("close", rec["sid"])[0], 0)
        self.assertIsNone(projects.load_project(self.slug)["orchestrator"])


class RotateTest(SessionTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch("foreman.delivery.deliver_pending", return_value="delivered")
        self.deliver = patcher.start()
        self.addCleanup(patcher.stop)

    def rotate(self, sid, *extra):
        return run_main("rotate", sid, "--transport", "fake", *extra)

    def test_rotate_opens_a_replacement_and_asks_the_old_one_for_a_handoff(self):
        from foreman import sessions
        old = self.new_session()
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 0, err)
        new = [r for r in self.sessions() if r["sid"] != old["sid"]][0]
        self.assertEqual((new["role"], new["purpose"], new["project"], new["agent"], new["model"]),
                         ("worker", "build the api", self.slug, "claude", "sonnet"))
        self.assertEqual(new["replaces"], old["sid"])
        self.assertEqual((new["state"], new["lifecycle"]), ("starting", "active"))
        self.assertEqual(new["title"], "fm-api-r2")
        self.assertIn(new["sid"], out)
        now = sessions.load_session(old["sid"])
        self.assertEqual((now["lifecycle"], now["state"], now["replaced_by"]),
                         ("rotated", "idle", new["sid"]))  # never put rotated into state
        self.assertTrue(self.fake.alive(old["transport"]["handle"]))  # left idle, not closed
        prompt = self.fake.inputs(new["transport"]["handle"])[0]
        self.assertIn(old["sid"], prompt)
        self.assertIn("replace", prompt)
        self.assertIn("handoff", prompt)
        self.assertIn("ONE", prompt)
        self.assertIn("foreman ask %s" % old["sid"], prompt)
        self.assertIn("hour", prompt)
        for step in ("re-read the handoff", "transcript", "why"):
            self.assertIn(step, prompt)
        self.assertIn("build the api", prompt)
        (msg,) = sessions.inbox_pending(old["sid"])
        self.assertIn("foreman send %s -" % new["sid"], msg["text"])
        self.assertIn("handoff", msg["text"])
        self.assertEqual(msg["from"], "foreman")
        self.deliver.assert_called_once_with(old["sid"])
        (row,) = self.journal()
        self.assertEqual(row["kind"], "rotate")
        self.assertIn(old["sid"], row["text"])
        self.assertIn(new["sid"], row["text"])

    def test_board_shows_old_arrow_new_after_rotation(self):
        from foreman import projects
        old = self.new_session()
        projects.add_task(self.slug, "Build API", stage="code", owner=old["sid"])
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 0, err)
        new = [r for r in self.sessions() if r["sid"] != old["sid"]][0]
        code, bout, _ = run_main("board", "show", "--project", self.slug)
        self.assertEqual(code, 0)
        self.assertIn("%s → %s" % (old["sid"], new["sid"]), bout)

    def test_model_may_change(self):
        old = self.new_session()
        code, out, err = self.rotate(old["sid"], "--model", "opus")
        self.assertEqual(code, 0, err)
        new = [r for r in self.sessions() if r["sid"] != old["sid"]][0]
        self.assertEqual((new["agent"], new["model"]), ("claude", "opus"))

    def test_agent_may_change(self):
        old = self.new_session()
        code, out, err = self.rotate(old["sid"], "--agent", "antigravity", "--model", "gemini-3.8-flash-high")
        self.assertEqual(code, 0, err)
        new = [r for r in self.sessions() if r["sid"] != old["sid"]][0]
        self.assertEqual((new["agent"], new["model"]), ("antigravity", "gemini-3.8-flash-high"))

    def test_busy_is_refused(self):
        old = self.new_session(state="busy")
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 6)
        self.assertEqual(len(self.sessions()), 1)
        self.deliver.assert_not_called()

    def test_undelivered_inbox_is_refused(self):
        from foreman import sessions
        old = self.new_session()
        sessions.inbox_enqueue(old["sid"], "finish this first")
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 8)
        self.assertIn("inbox", err.lower() + " " + out.lower())
        self.assertEqual(len(self.sessions()), 1)
        self.deliver.assert_not_called()

    def test_already_rotated_or_closed_or_gone(self):
        old_rot = self.new_session(lifecycle="rotated", title="t-rot")
        old_cls = self.new_session(lifecycle="closed", title="t-cls")
        old_gone = self.new_session(state="gone", title="t-gone")
        for rec in (old_rot, old_cls, old_gone):
            code, out, err = self.rotate(rec["sid"])
            self.assertEqual(code, 2, rec["title"])
        self.assertEqual(len(self.sessions()), 3)

    def test_gone_terminal_opens_nothing(self):
        old = self.new_session()
        self.fake.kill(old["transport"]["handle"])
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 3)
        self.assertIn("terminal gone", err)
        self.assertEqual(len(self.sessions()), 1)

    def test_stop_on_the_same_provider_is_refused(self):
        old = self.new_session()
        self.levels.return_value = {"claude": "stop", "antigravity": "ok"}
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 5)
        self.assertEqual(len(self.sessions()), 1)
        code, out, err = self.rotate(old["sid"], "--agent", "antigravity", "--model", "gemini-3.8-flash-high")
        self.assertEqual(code, 0, err)

    def test_orchestrator_rotation_keeps_the_role_and_updates_the_project(self):
        from foreman import projects
        old = self.new_session(role="orchestrator", owner="user", purpose="orchestrator of demo", title="demo-orch")
        proj = projects.load_project(self.slug)
        proj["orchestrator"] = old["sid"]
        projects.save_project(proj)
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 0, err)
        new = [r for r in self.sessions() if r["sid"] != old["sid"]][0]
        self.assertEqual((new["role"], new["owner"], new["title"]), ("orchestrator", "user", "demo-orch-r2"))
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], new["sid"])
        prompt = self.fake.inputs(new["transport"]["handle"])[0]
        self.assertIn("foreman-orchestrator", prompt)  # the briefing comes first
        self.assertIn(old["sid"], prompt)

    def test_title_counter_and_explicit_title(self):
        old = self.new_session(title="fm-api-r2")
        self.rotate(old["sid"])
        titles = sorted(r["title"] for r in self.sessions())
        self.assertEqual(titles, ["fm-api-r2", "fm-api-r3"])
        old2 = self.new_session(title="other")
        self.rotate(old2["sid"], "--title", "renamed")
        self.assertIn("renamed", [r["title"] for r in self.sessions()])

    def test_gone_after_open_still_reports(self):
        self.deliver.return_value = "terminal_gone"
        old = self.new_session()
        code, out, err = self.rotate(old["sid"])
        self.assertEqual(code, 0)
        self.assertIn("terminal gone", out + err)

    def test_ask_to_rotated_session_warns_when_cache_is_cold(self):
        from foreman import sessions
        os.environ["FOREMAN_NOW"] = "2026-11-05T11:00:00Z"
        old = self.new_session()
        sessions.update_session(old["sid"], last_event_at="2026-11-05T11:00:00Z")
        self.assertEqual(self.rotate(old["sid"])[0], 0)
        sessions.inbox_mark_delivered(old["sid"])
        os.environ["FOREMAN_NOW"] = "2026-11-05T13:00:00Z"
        code, out, err = run_main("ask", old["sid"], "why did you pick X?")
        self.assertEqual(code, 0, err)
        self.assertIn("cold", err)
        self.assertIn(old["sid"], err)

    def test_status_shows_rotated_arrow_and_hides_closed_lifecycle(self):
        old = self.new_session(title="worker-a")
        self.assertEqual(self.rotate(old["sid"])[0], 0)
        new = [r for r in self.sessions() if r["sid"] != old["sid"]][0]
        closed = self.new_session(title="done-worker")
        self.assertEqual(run_main("close", closed["sid"])[0], 0)
        code, out, err = run_main("status", "--project", self.slug)
        self.assertEqual(code, 0, err)
        self.assertNotIn("done-worker", out)
        row = [l for l in out.splitlines() if "worker-a " in l][0]
        self.assertIn("rotated", row)
        self.assertIn(new["sid"], row)


class OrchestratorTest(SessionTestCase):
    def start(self, *extra):
        return run_main("orchestrator", "start", "--project", self.slug, "--transport", "fake", *extra)

    def test_start(self):
        from foreman import projects
        code, out, err = self.start()
        self.assertEqual(code, 0, err)
        (rec,) = self.sessions()
        self.assertEqual((rec["role"], rec["agent"], rec["model"], rec["state"], rec["lifecycle"]),
                         ("orchestrator", "claude", "sonnet", "starting", "active"))
        self.assertEqual(rec["owner"], "user")
        self.assertEqual(rec["title"], "%s-orch" % self.slug)
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], rec["sid"])
        self.assertIn(rec["sid"], out)
        prompt = self.fake.inputs(rec["transport"]["handle"])[0]
        self.assertIn("foreman-orchestrator", prompt)
        self.assertIn("foreman say", prompt)
        self.assertIn("foreman watch list", prompt)
        self.assertIn(self.slug, prompt)
        self.assertIn(self.project["path"], prompt)
        self.assertNotIn("{{", prompt)
        (row,) = self.journal()
        self.assertEqual(row["kind"], "open")

    def test_antigravity_is_accepted(self):
        code, out, err = self.start("--agent", "antigravity", "--model", "gemini-4-argon-medium")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.sessions()[0]["agent"], "antigravity")

    def test_codex_is_worker_only(self):
        code, out, err = self.start("--agent", "codex")
        self.assertEqual(code, 2)
        self.assertIn("worker", err)
        self.assertEqual(self.sessions(), [])

    def test_unknown_agent(self):
        self.assertEqual(self.start("--agent", "vim")[0], 2)

    def test_second_orchestrator_is_refused_until_the_first_is_closed(self):
        self.assertEqual(self.start()[0], 0)
        code, out, err = self.start()
        self.assertEqual(code, 2)
        self.assertIn("already", err)
        sid = self.sessions()[0]["sid"]
        self.assertEqual(run_main("close", sid, "--user-consent")[0], 0)
        self.assertEqual(self.start()[0], 0)

    def test_limits_apply(self):
        self.levels.return_value = {"claude": "stop", "antigravity": "ok"}
        code, out, err = self.start()
        self.assertEqual(code, 5)
        self.assertEqual(self.sessions(), [])

    def test_show(self):
        code, out, err = run_main("orchestrator", "show", "--project", self.slug)
        self.assertEqual(code, 0)
        self.assertIn("none", out)
        self.start()
        code, out, err = run_main("orchestrator", "show", "--project", self.slug)
        self.assertIn(self.sessions()[0]["sid"], out)

    def test_adopt_registered_worker_session(self):
        from foreman import projects, sessions
        w = self.new_session(role="worker", owner="user", title="my-claude")
        code, out, err = run_main("orchestrator", "adopt", w["sid"], "--project", self.slug)
        self.assertEqual(code, 0, err)
        self.assertIn(w["sid"], out)
        now = sessions.load_session(w["sid"])
        self.assertEqual((now["role"], now["owner"], now["lifecycle"]), ("orchestrator", "user", "active"))
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], w["sid"])

    def test_adopt_live_unregistered_transport_handle(self):
        from foreman import projects
        handle = self.fake.launch("claude", "sonnet", None, self.project["path"], "", "live-tab")
        code, out, err = run_main("orchestrator", "adopt", handle, "--project", self.slug, "--transport", "fake")
        self.assertEqual(code, 0, err)
        (rec,) = self.sessions()
        self.assertEqual((rec["role"], rec["owner"], rec["agent"]), ("orchestrator", "user", "claude"))
        self.assertEqual(rec["transport"], {"kind": "fake", "handle": handle})
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], rec["sid"])
        self.assertIn(rec["sid"], out)

    def test_adopt_refuses_when_live_orchestrator_already_exists(self):
        self.assertEqual(self.start()[0], 0)
        w = self.new_session(role="worker", owner="user", title="second")
        code, out, err = run_main("orchestrator", "adopt", w["sid"], "--project", self.slug)
        self.assertEqual(code, 2)
        self.assertIn("already", err)

    def test_adopt_refuses_plain_shell_handle(self):
        # Unregistered plain shell (no agent reported by transport)
        handle = self.fake.launch(None, None, None, self.project["path"], "", "plain-sh")
        code, out, err = run_main("orchestrator", "adopt", handle, "--project", self.slug, "--transport", "fake")
        self.assertEqual(code, 2)
        self.assertIn("shell", err.lower())
        self.assertEqual(self.sessions(), [])
        # Registered session whose terminal reports no agent (plain shell)
        w = self.new_session(role="worker", owner="user", title="became-sh")
        self.fake._modify(w["transport"]["handle"], lambda r: r.update(agent=None))
        code, out, err = run_main("orchestrator", "adopt", w["sid"], "--project", self.slug)
        self.assertEqual(code, 2)
        self.assertIn("shell", err.lower())

    def test_adopt_refuses_worker_only_agent(self):
        w = self.new_session(agent="codex", model=None, title="codex-tab")
        code, out, err = run_main("orchestrator", "adopt", w["sid"], "--project", self.slug)
        self.assertEqual(code, 2)
        self.assertIn("worker", err.lower())

    def test_adopt_is_blocked_by_orchestrator_guard(self):
        from foreman import guard
        self.assertIsNotNone(guard.check_command("foreman orchestrator adopt s-123456", project=self.slug))

    def test_briefing_template(self):
        from foreman.commands import orchestrator
        path = orchestrator.template_path()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertLessEqual(len(text.splitlines()), 60)
        for needle in ("foreman-orchestrator", "foreman say", "foreman watch list", "foreman limits --line",
                       "Russian", "foreman open", "foreman rotate", "foreman close"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
