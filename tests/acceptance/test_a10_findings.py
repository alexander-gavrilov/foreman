"""Spec §8.10 — a finding reported from the work Mac stays a local draft; on a public issue it contains no project
data.

Drafts are made through the real CLI on a work-kind machine with the DEFAULT upstream mode (nothing configured), and
the `gh` shim proves that nothing was posted. The public issue text is built in-process with an injected runner that
captures the `gh issue create` argv instead of running it (`findings.default_runner` is rigged to fail the test if
anything tries to run a real `gh`)."""
import os
import socket
import unittest
from unittest import mock

from tests.acceptance.common import AcceptanceCase

# What a careless agent pastes into a finding, seeded with this machine's and this project's private data.
SECRET_FORMS = (  # (the credential as pasted, the part that must not survive)
    ("ghp_1A2B3C4D5E6F7G8H9I0J1K2L3M4N5O6P7Q8R", "3C4D5E6F7G8H9I0J1K2L3M4N5O6P7Q8R"),   # GitHub token
    ("sk-ant-api03-Zk3Qw9TnRv5LpXc2HbYd8UeA", "Zk3Qw9TnRv5LpXc2HbYd8UeA"),               # Anthropic API key
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.dBjftJeZ4CVPmB92K27u", "dBjftJeZ4CVPmB92K27u"),  # bearer JWT
    ("123456789:AAFh3kLmNoPqRsTuVwXyZ-abcdefghijk", "AAFh3kLmNoPqRsTuVwXyZ-abcdefghijk"),  # Telegram bot token
)


class FindingsTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.write_config({"machine": {"name": "work-mac", "kind": "work"}})  # no upstream.mode: the default
        self.slug, self.path = self.make_project("acme-billing")
        self.host = socket.gethostname()
        self.seeded = {
            "project path": os.path.join(self.path, "src", "invoice_service.py"),
            "project dir": self.path,
            "project name": "acme-billing",
            "home path": os.path.join(self.home, ".claude", "settings.json"),
            "machine name": "work-mac",
            "hostname": "buildbox.corp",
            "email": "jane.doe@acme-corp.com",
            "session id": "s-a1b2c3",
            "hex id": "9f86d081884c7d65",
            "private url": "https://git.acme-corp.com/billing/invoices/-/merge_requests/42",
            "code line": "    return db.execute('SELECT * FROM invoices WHERE total > 100')",
        }

    def text(self):
        s = self.seeded
        return ("orchestrator wake is delayed when the hook runs in %(project path)s\n"
                "seen on %(machine name)s (%(hostname)s) by %(email)s, session %(session id)s / %(hex id)s\n"
                "project %(project name)s lives in %(project dir)s and config is %(home path)s\n"
                "see %(private url)s\n```\n%(code line)s\n```\n"
                "%(code line)s\n"
                "Expected: the digest arrives at the next idle point." % s)

    def assert_clean(self, text, label):
        for what, value in self.seeded.items():
            self.assertNotIn(value, text, "%s leaked the %s" % (label, what))
        self.assertNotIn(self.host, text)
        for fragment in ("acme-billing", "acme-corp", "work-mac", "buildbox", "invoice_service", "SELECT"):
            self.assertNotIn(fragment, text, "%s leaked %r" % (label, fragment))

    # ------------------------------------------------------------------------------ draft on the work Mac

    def test_on_a_work_machine_a_finding_stays_a_local_draft(self):
        from foreman import config
        self.assertEqual(config.get("upstream.mode"), "draft")
        out = self.fm_ok("report", self.text())
        self.assertIn("saved as draft", out)
        self.assertEqual(self.shim_calls("gh"), [], "nothing may be posted from the work machine")
        self.assertEqual(self.shim_calls(), [])
        listing = self.fm_ok("report", "--list")
        self.assertRegex(listing, r"^f-\w+\s+draft\s+")
        from foreman import findings
        [finding] = findings.list_findings()
        self.assertEqual((finding["status"], finding["url"], finding["kind"]), ("draft", None, "foreman"))
        # the draft on disk is already stripped: even the local inbox holds no project data
        self.assert_clean(finding["body"], "the draft")
        self.assertIn("orchestrator wake is delayed", finding["body"])

    def test_a_draft_is_sent_only_when_the_user_says_so_and_then_only_scrubbed(self):
        from foreman import findings
        self.fm_ok("report", self.text())
        [draft] = findings.list_findings()
        calls = []

        def runner(argv):
            calls.append(argv)
            return 0, "https://github.com/alexander-gavrilov/foreman/issues/7\n", ""

        with mock.patch.object(findings, "default_runner", side_effect=AssertionError("a real gh was run")):
            sent = findings.send(draft["id"], runner=runner)
        self.assertEqual((sent["status"], sent["url"]), ("sent", "https://github.com/alexander-gavrilov/foreman/issues/7"))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:5], ["gh", "issue", "create", "-R", "alexander-gavrilov/foreman"])
        self.assert_clean(" ".join(calls[0]), "the gh argv")

    def test_a_project_finding_never_leaves_the_machine(self):
        out = self.fm_ok("report", "--kind", "project", "--slug", self.slug, "always run the linter before review")
        self.assertIn("noted in the lessons", out)
        from foreman import findings, paths
        self.assertEqual(findings.list_findings(), [])
        with open(paths.lessons_file(self.slug)) as f:
            self.assertIn("always run the linter", f.read())
        self.assertEqual(self.shim_calls(), [])

    # ------------------------------------------------------------------------------ the public issue

    def build_public_issue(self, text):
        """Mode `auto` (the home default): build the `gh issue create` argv with an injected runner."""
        from foreman import findings
        self.update_config(upstream={"mode": "auto"})
        calls = []

        def runner(argv):
            calls.append(list(argv))
            return 0, "https://github.com/alexander-gavrilov/foreman/issues/8\n", ""

        with mock.patch.object(findings, "default_runner", side_effect=AssertionError("a real gh was run")):
            finding = findings.report_foreman(text, runner=runner)
        self.assertEqual(finding["status"], "sent")
        self.assertEqual(len(calls), 1)
        return calls[0]

    def test_the_public_issue_text_contains_no_project_data(self):
        argv = self.build_public_issue(self.text())
        self.assertEqual(argv[:5], ["gh", "issue", "create", "-R", "alexander-gavrilov/foreman"])
        title = argv[argv.index("--title") + 1]
        body = argv[argv.index("--body") + 1]
        self.assert_clean(title + "\n" + body, "the public issue")
        # what is useful to the maintainers survives
        self.assertIn("orchestrator wake is delayed", body)
        self.assertIn("Expected: the digest arrives at the next idle point.", body)
        self.assertEqual(self.shim_calls(), [])

    def test_a_pasted_log_with_code_and_ids_is_reduced_to_the_symptom(self):
        log = ("Traceback (most recent call last):\n  File \"%s\", line 12, in run\n    boom()\nValueError: x\n"
               "session s-ffee11 thread 123e4567-e89b-12d3-a456-426614174000\nthe hook returned {} twice"
               % os.path.join(self.path, "src", "app.py"))
        body = self.build_public_issue(log)[-1]
        self.assertNotIn("Traceback", body)
        self.assertNotIn("app.py", body)
        self.assertNotIn("s-ffee11", body)
        self.assertNotIn("123e4567", body)
        self.assertIn("the hook returned {} twice", body)

    # Regression guard (was a product bug): scrub used to mask paths, names and ids but not credentials.
    # Spec §8.10 / §4.11: no secret may leave the machine in a public issue.
    def test_a_pasted_credential_does_not_reach_the_public_issue(self):
        for secret, essential in SECRET_FORMS:
            with self.subTest(secret=secret[:12]):
                argv = self.build_public_issue("the hook failed while using %s for the call" % secret)
                self.assertNotIn(essential, " ".join(argv))


if __name__ == "__main__":
    unittest.main()
