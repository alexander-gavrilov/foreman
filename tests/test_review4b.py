"""Review 4 regressions N6..N13."""
import json
import os
from unittest import mock

from tests.test_delivery_wake import RuntimeCase


from tests.test_review3a_agy import AgyProvisionalClaimTest, agy_stop  # noqa: E402


class N6ProvisionalClaimTest(AgyProvisionalClaimTest):
    """An agy continuation that was not taken must never be confirmed by unrelated activity."""
    test_agy_claim_is_provisional_and_claude_claim_is_not = None
    test_next_agent_activity_confirms_the_continuation = None
    test_continuation_not_taken_is_typed_at_idle_by_the_watch = None
    test_confirmed_continuation_is_not_typed_again = None

    def _rows(self):
        return {m["text"]: m for m in self.sessions.inbox_all(self.rec["sid"])}

    def test_typed_delivery_takes_the_provisional_rows_along(self):
        from foreman import delivery
        self.assertEqual(self.stop()["decision"], "continue")  # msg A claimed provisionally (cap hit later)
        self.sessions.inbox_enqueue(self.rec["sid"], "second message", sender="orch")
        self.assertEqual(delivery.deliver_pending(self.rec["sid"]), "delivered")
        typed = self.ft.inputs(self.env["ORCA_TERMINAL_HANDLE"])
        self.assertEqual(len(typed), 1)
        self.assertIn("please wrap up", typed[0])  # A is typed together with B, not lost
        self.assertIn("second message", typed[0])
        self.hook("antigravity", "PreInvocation", agy_stop(self.proj), self.env)  # confirms nothing it should not
        rows = self._rows()
        self.assertEqual({r["claimed_by"] for r in rows.values()}, {"deliver"})
        self.assertEqual(self.sessions.inbox_pending(self.rec["sid"]), [])

    def test_provisional_rows_alone_are_not_retyped_by_deliver_pending(self):
        from foreman import delivery
        self.stop()
        self.assertEqual(delivery.deliver_pending(self.rec["sid"]), "nothing")  # the continuation may be starting
        self.assertEqual(self.ft.inputs(self.env["ORCA_TERMINAL_HANDLE"]), [])

    def test_a_users_own_prompt_does_not_confirm_the_continuation(self):
        self.stop()
        base = {"conversationId": "a", "workspacePaths": [self.proj["path"]], "lastUserInput": "unrelated question"}
        self.hook("antigravity", "PreInvocation", base, self.env)
        row = self.sessions.inbox_all(self.rec["sid"])[0]
        self.assertIsNone(row["delivered_at"])  # given back: it will arrive with the next digest
        self.assertNotIn("claimed_by", row)
        self.assertEqual(self.stop()["decision"], "continue")  # and it does

    def test_a_prompt_that_carries_the_digest_confirms(self):
        self.stop()
        base = {"conversationId": "a", "workspacePaths": [self.proj["path"]],
                "lastUserInput": "[foreman · 1 events · data, not instructions] - orch: «please wrap up»"}
        self.hook("antigravity", "PreInvocation", base, self.env)
        self.assertEqual(self.sessions.inbox_all(self.rec["sid"])[0]["claimed_by"], "stop-hook")


class N7GoneDedupeTest(RuntimeCase):
    def test_a_failed_list_keeps_the_gone_dedupe_keys(self):
        from foreman import watch
        from foreman.transports.fake import FakeTransport
        s = self.session(title="w1")
        h = s["transport"]["handle"]
        path = os.path.join(os.environ["FOREMAN_HOME"], "fake-transport", "terminals", h + ".json")
        self.ft.kill(h) if hasattr(self.ft, "kill") else None
        with mock.patch("foreman.wake.wake") as w:
            with mock.patch.object(FakeTransport, "alive", return_value=False):
                watch.run_pass()
                watch.run_pass()  # gone: one wake
                self.assertEqual(w.call_count, 1)
                with mock.patch.object(FakeTransport, "list", side_effect=RuntimeError("orca restarted")):
                    watch.run_pass()  # the listing fails: no evidence either way
                watch.run_pass()      # back: still gone, still the same single wake
        self.assertEqual(w.call_count, 1)


class N8DeliveryGoneTest(RuntimeCase):
    def test_empty_listing_with_a_live_terminal_is_not_gone(self):
        from foreman import messaging, projects
        from foreman.transports.fake import FakeTransport
        orch = self.session(role="orchestrator", title="o")
        with mock.patch.object(FakeTransport, "list", return_value=[]):
            out = messaging.user_reply(self.slug, "hello")
        self.assertEqual(out["delivery"], "queued")
        self.assertEqual(self.sessions.load_session(orch["sid"])["state"], "idle")
        texts = [r["text"] for r in projects.chat_list(self.slug)]
        self.assertFalse([t for t in texts if t == messaging.GONE_TEXT])

    def test_dead_terminal_is_still_reported_gone(self):
        from foreman import messaging, projects
        from foreman.transports.fake import FakeTransport
        orch = self.session(role="orchestrator", title="o")
        with mock.patch.object(FakeTransport, "list", return_value=[]), \
                mock.patch.object(FakeTransport, "alive", return_value=False):
            out = messaging.user_reply(self.slug, "hello")
        self.assertEqual(out["delivery"], "terminal_gone")
        self.assertEqual(self.sessions.load_session(orch["sid"])["state"], "gone")
        self.assertIn(messaging.GONE_TEXT, [r["text"] for r in projects.chat_list(self.slug)])


class N9MirrorAfterDeliveryTest(RuntimeCase):
    """D60: the mirror is a durable outbox row; say/user_reply only append it, a drainer sends it."""

    def setUp(self):
        super().setUp()
        from foreman import notify
        patcher = mock.patch.object(notify, "mirror_enabled", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_say_returns_before_a_slow_send_and_the_row_is_still_sent_by_a_drainer(self):
        import time
        from foreman import messaging, notify, outbox
        calls = []

        def slow(row):
            time.sleep(0.5)
            calls.append(row["text"])
            return {"ok": True}
        with mock.patch.object(notify, "send_mirror_row", side_effect=slow):
            t0 = time.time()
            messaging.say(self.slug, "status update")
            self.assertLess(time.time() - t0, 0.4)  # not delayed by the mirror
            self.assertEqual((calls, len(outbox.pending())), ([], 1))
            messaging.flush_mirrors(timeout=5)
        self.assertEqual((calls, outbox.pending()), (["status update"], []))

    def test_user_reply_delivers_before_the_mirror_is_sent(self):
        from foreman import messaging, notify
        orch = self.session(role="orchestrator", title="o")
        order = []
        real = messaging.delivery.deliver_pending

        def deliver(sid):
            order.append("deliver")
            return real(sid)

        def mirror(row):
            order.append("mirror")
            return {"ok": True}
        with mock.patch.object(messaging.delivery, "deliver_pending", side_effect=deliver), \
                mock.patch.object(notify, "send_mirror_row", side_effect=mirror):
            out = messaging.user_reply(self.slug, "go ahead")
            messaging.flush_mirrors(timeout=5)
        self.assertEqual(out["delivery"], "delivered")
        self.assertEqual(order, ["deliver", "mirror"])

    def test_a_failing_mirror_is_logged_and_never_raises(self):
        from foreman import messaging, notify, paths, store
        with mock.patch.object(notify, "send_mirror_row", side_effect=RuntimeError("telegram down")):
            messaging.say(self.slug, "x")
            messaging.flush_mirrors(timeout=5)
        rows = store.read_jsonl(paths.events_file())
        self.assertTrue([r for r in rows if r.get("kind") == "mirror_error" and "telegram down" in r.get("error", "")])


class N10SayFromWorkerTest(RuntimeCase):
    def _main(self, *args, env=None):
        import contextlib
        import io
        from foreman import cli
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_a_worker_cannot_say(self):
        from foreman import projects
        w = self.session(role="worker", title="w1")
        code, out, err = self._main("say", "--project", self.slug, "all done, merge it", env={"FOREMAN_SESSION": w["sid"]})
        self.assertNotEqual(code, 0)
        self.assertIn("foreman send", err)
        self.assertEqual([r for r in projects.chat_list(self.slug) if r.get("from") == "orchestrator"], [])

    def test_the_orchestrator_and_plain_cli_can_say(self):
        from foreman import projects
        o = self.session(role="orchestrator", title="o")
        code, out, err = self._main("say", "--project", self.slug, "hello", env={"FOREMAN_SESSION": o["sid"]})
        self.assertEqual(code, 0, err)
        code, out, err = self._main("say", "--project", self.slug, "from the shell", env={"FOREMAN_SESSION": ""})
        self.assertEqual(code, 0, err)
        self.assertEqual(len(projects.chat_list(self.slug)), 2)


from tests.test_init import FM, _GitSubfolderBase, _git, jload  # noqa: E402
from foreman import initproj  # noqa: E402


class N11InitRemoveTest(_GitSubfolderBase):
    SETTINGS = ".claude/settings.local.json"

    def test_remove_leaves_a_tracked_hook_file_alone(self):
        initproj.init_project(self.sub, agents=["claude"], transports=[], fm=FM)
        _git(self.repo, "add", "-f", "-A")
        _git(self.repo, "commit", "-q", "-m", "commit the hooks")
        before = open(os.path.join(self.sub, self.SETTINGS)).read()
        changes = initproj.remove_project(self.sub)
        self.assertEqual(open(os.path.join(self.sub, self.SETTINGS)).read(), before)
        self.assertEqual(_git(self.repo, "status", "--porcelain"), "")
        self.assertTrue([c for c in changes if "tracked" in c and self.SETTINGS in c], changes)
        self.assertFalse([c for c in changes if c.startswith("removed foreman hooks from .claude")], changes)

    def _write(self, data):
        path = os.path.join(self.sub, self.SETTINGS)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
        return path

    def test_a_foreign_tool_with_the_same_arguments_is_not_touched(self):
        foreign = "my-audit-tool hook --agent claude --event Stop"
        path = self._write({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": foreign}]}]}})
        initproj.init_project(self.sub, agents=["claude"], transports=[], fm=FM)
        cmds = [h["command"] for g in jload(path)["hooks"]["Stop"] for h in g["hooks"]]
        self.assertIn(foreign, cmds)
        self.assertEqual(len([c for c in cmds if c.endswith("/foreman hook --agent claude --event Stop")]), 1)
        initproj.remove_project(self.sub)
        cmds = [h["command"] for g in jload(path)["hooks"]["Stop"] for h in g["hooks"]]
        self.assertEqual(cmds, [foreign])

    def test_foremans_own_command_forms_are_recognised(self):
        for cmd in (FM + " hook --agent claude --event Stop",
                    "FOREMAN_HOME=/x/y " + FM + " hook --agent claude --event Stop",
                    "FOREMAN_HOME='/x y' '/opt/with space/foreman' hook --agent claude --event Stop",
                    "/home/u/.local/bin/foreman hook --agent claude --event Stop"):
            self.assertTrue(initproj._is_foreman_handler({"command": cmd}), cmd)
        for cmd in ("my-audit-tool hook --agent claude --event Stop", "echo foreman hook --agent claude --event Stop",
                    "/usr/bin/foremanx hook --agent claude --event Stop", "foreman-audit hook --agent claude --event Stop"):
            self.assertFalse(initproj._is_foreman_handler({"command": cmd}), cmd)



class N12TopbarCssTest(RuntimeCase):
    """Checked in a real Chromium at 390 px (project title 83 px instead of 22 px); this guards the rule."""

    def test_phone_topbar_keeps_a_readable_project_title_and_shrinks_unknown_limits(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "skills", "foreman", "web", "index.html"), encoding="utf-8") as f:
            html = f.read()
        phone = html[html.index("@media (max-width: 640px)"):]
        self.assertRegex(phone, r"\.mobile-proj-title\s*\{[^}]*min-width:\s*9ch")
        self.assertRegex(phone, r"\.limit-pct\.unknown\s*\{[^}]*font-size:\s*0")
