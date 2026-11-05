import json
import os
import stat

from tests.helpers import ForemanTestCase, run_cli
from foreman import findings, paths  # noqa: E402
from foreman.commands import report as report_cmd  # noqa: E402


class FakeGh:
    def __init__(self, rc=0, out="https://github.com/alexander-gavrilov/foreman/issues/7\n", err=""):
        self.calls, self.rc, self.out, self.err = [], rc, out, err

    def __call__(self, argv):
        self.calls.append(argv)
        return self.rc, self.out, self.err


class ReportModes(ForemanTestCase):
    def test_auto_sends_issue(self):
        self.write_config({"upstream": {"mode": "auto"}})
        gh = FakeGh()
        f = findings.report_foreman("hook exits 1 on empty stdin", runner=gh)
        self.assertEqual(f["status"], "sent")
        self.assertEqual(f["url"], "https://github.com/alexander-gavrilov/foreman/issues/7")
        argv = gh.calls[0]
        self.assertEqual(argv[:6], ["gh", "issue", "create", "-R", "alexander-gavrilov/foreman", "--title"])
        self.assertEqual(argv[6], "hook exits 1 on empty stdin")
        self.assertEqual(argv[7], "--body")
        saved = json.load(open(paths.finding_file(f["id"])))
        for key in ("id", "ts", "title", "body", "status", "url"):
            self.assertIn(key, saved)

    def test_auto_failure_is_failed_draft(self):
        self.write_config({"upstream": {"mode": "auto"}})
        f = findings.report_foreman("x problem", runner=FakeGh(rc=1, err="gh: not logged in"))
        self.assertEqual(f["status"], "failed")
        self.assertIsNone(f["url"])
        self.assertTrue(os.path.exists(paths.finding_file(f["id"])))
        # the user can retry it
        again = findings.send(f["id"], runner=FakeGh())
        self.assertEqual(again["status"], "sent")

    def test_draft_does_not_call_gh(self):
        self.write_config({"upstream": {"mode": "draft"}})
        gh = FakeGh()
        f = findings.report_foreman("a problem", runner=gh)
        self.assertEqual((f["status"], gh.calls), ("draft", []))
        sent = findings.send(f["id"], runner=gh)
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(len(gh.calls), 1)
        self.assertEqual(findings.load(f["id"])["status"], "sent")

    def test_off_is_local_only(self):
        self.write_config({"upstream": {"mode": "off"}})
        gh = FakeGh()
        f = findings.report_foreman("a problem", runner=gh)
        self.assertEqual((f["status"], gh.calls), ("off", []))

    def test_work_machine_defaults_to_draft_and_scrubs_project_data(self):
        proj = self.init_project("Secret Ledger")
        self.write_config({"machine": {"name": "workmac", "kind": "work"}})
        gh = FakeGh()
        text = "tool failed in %s/src/app.py for Secret Ledger (%s) on workmac" % (proj["path"], proj["slug"])
        f = findings.report_foreman(text, runner=gh)
        self.assertEqual(f["status"], "draft")
        self.assertEqual(gh.calls, [])
        for leak in (proj["path"], "ledger", "secret", "workmac", self.work):
            self.assertNotIn(leak.lower(), f["body"].lower())
        self.assertIn("<path>", f["body"])
        self.assertIn("<project>", f["body"])

    def test_list_ordering(self):
        self.write_config({"upstream": {"mode": "off"}})
        a = findings.report_foreman("first", runner=FakeGh())
        self.assertEqual([x["id"] for x in findings.list_findings()], [a["id"]])

    def test_send_unknown(self):
        self.assertIsNone(findings.send("f-nope"))


class ReportProject(ForemanTestCase):
    def test_lessons_entry(self):
        proj = self.init_project("demo")
        os.environ["FOREMAN_NOW"] = "2026-11-05T10:00:00Z"
        findings.report_project(proj["slug"], "always run tests first\nsecond line")
        findings.report_project(proj["slug"], "another")
        text = open(paths.lessons_file(proj["slug"])).read()
        self.assertEqual(text, "# Lessons\n\n- 2026-11-05 always run tests first\n  second line\n"
                               "- 2026-11-05 another\n")


class ReportCli(ForemanTestCase):
    def test_cli_project_and_draft_and_list(self):
        proj = self.init_project("demo")
        self.write_config({"upstream": {"mode": "draft"}})
        code, out, err = run_cli("report", "keep PRs small", "--project", cwd=proj["path"])
        self.assertEqual(code, 0, err)
        self.assertIn("lessons", out)
        code, out, err = run_cli("report", "init is slow in demo", cwd=proj["path"])
        self.assertEqual(code, 0, err)
        self.assertIn("saved as draft", out)
        self.assertNotIn("demo", json.dumps(findings.list_findings()))
        code, out, err = run_cli("report", "--list")
        self.assertIn("draft", out)

    def test_cli_send_with_fake_gh_on_path(self):
        self.write_config({"upstream": {"mode": "draft"}})
        bindir = self.make_dir("fakebin")
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write("#!/bin/sh\necho https://github.com/alexander-gavrilov/foreman/issues/9\n")
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IXUSR)
        env = {"PATH": bindir + os.pathsep + os.environ["PATH"]}
        code, out, err = run_cli("report", "a bug", env=env)
        fid = findings.list_findings()[0]["id"]
        code, out, err = run_cli("report", "--send", fid, env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("issues/9", out)
