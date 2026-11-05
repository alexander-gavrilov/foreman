"""Review 4 N3 (over-scrubbing files empty issues) and N4 (secrets that still leak), D56 gate."""
import unittest
from unittest import mock

from tests.helpers import ForemanTestCase
from foreman import scrub  # noqa: E402

CTX = {"homes": ["/home/alex"], "paths": ["/home/alex/code/shop-app"], "names": ["shop-app"], "hosts": ["alexs-mac"]}

PARA = ("The orchestrator did not wake after the worker finished, although the hook fired and the digest was queued; "
        "the terminal stayed idle for several minutes and the board kept showing the session as busy, which made "
        "the whole project look stuck while nothing was wrong with the worker itself, and the same happened twice.")

KEPT = [  # N3: ordinary text that must come through unchanged
    ("long paragraph", PARA),
    ("bullet list", "Steps:\n- start the orchestrator\n- close the worker terminal\n- watch the board"),
    ("star bullets", "Notes:\n* the digest is empty\n* the wake is late"),
    ("numbered list", "1. open the project\n2. run foreman status"),
    ("line ending with semicolon", "the hook fired first; the record came later;\nso the guard was off"),
    ("prose with equals", "x = 1 is what the board shows after the second pass"),
    ("context tokens", "context_tokens: 150000 at the time of the wake"),
    ("hotkey", "Hotkey: ctrl+c does nothing in the terminal"),
    ("key value", "key: value pairs are shown in the footer"),
    ("log.info", "the message appears in log.info only"),
    ("version", "foreman 3.12.1.2 build ran"),
    ("version with v", "since v1.4.239.1 the launch handle is nested"),
    ("foreman module names", "foreman.watch and foreman.sessions disagree"),
    ("foreman own path", "see skills/foreman/lib/foreman/watch.py for the pass"),
    ("tokens plural", "max_tokens: 4096 was reached"),
]

LEAKS = [  # N4: (label, text, fragment that must not survive)
    ("pem", "key:\n-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu1SU1LfVLPHCozMxH2Mo4lgOEePzNm0tRgeLezV6ffAt0gun\n"
            "VTLw7onLRnrq0/IzW7yWR7QkrmBL7jTKEn5u+qKhbwKfBstIs+bMY2Zkp18gnTxK\n-----END RSA PRIVATE KEY-----\nend",
     "MIIEow"),
    ("pem without end", "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n",
     "b3BlbnNz"),
    ("quoted password", 'config {"password": "hunter2"} failed', "hunter2"),
    ("quoted password single", "cfg 'password': 'hunter2' failed", "hunter2"),
    ("quoted password spaces", 'cfg password="my pass phrase 9" failed', "pass phrase"),
    ("basic auth", "sent Authorization: Basic dXNlcjpodW50ZXIy ok", "dXNlcjpo"),
    ("stripe live", "key sk_" "live_4eC39HqLyjWDarjtT1zdp7dc leaked", "4eC39Hq"),
    ("stripe test", "key sk_" "test_4eC39HqLyjWDarjtT1zdp7dc leaked", "4eC39Hq"),
    ("google api key", "key AIzaSyA-1234567890abcdefghijklmnopqrstuv leaked", "AIzaSy"),
    ("gitlab pat", "tok glpat-xxxxxxxxxxxxxxxxxxxx leaked", "glpat"),
    ("npm token", "tok npm_abcdefghijklmnopqrstuvwxyz0123456789 leaked", "npm_abc"),
    ("password is", "the password is hunter2 for the db", "hunter2"),
    ("host port", "connect to db01:5432 failed", "db01"),
    ("wildcard example host", "cert for *.example is bad", "*.example"),
    ("unknown prefix token", "token zx_Qw3rTy9uIo5pAs7dFg1hJk4lZx8cVb2 leaked", "Qw3rTy9u"),
    ("high entropy plain", "used 9fK2mQx7LpR4vNb8Zc1TyUe6WsAd3GhJ in the call", "9fK2mQx7"),
    ("url creds", "clone https://bob:s3cretpw@git.corp.io/x/y.git fails", "s3cretpw"),
]


class N3KeepOrdinaryText(unittest.TestCase):
    def test_kept_unchanged(self):
        for label, raw in KEPT:
            with self.subTest(label):
                self.assertEqual(scrub.scrub(raw, CTX), raw)

    def test_code_runs_and_strong_shapes_are_still_omitted(self):
        self.assertEqual(scrub.scrub("a\n    x = 1\n    y = 2\nb", CTX), "a\n<content omitted>\nb")
        self.assertEqual(scrub.scrub("a\n{\n  \"k\": 1\n}\nb", CTX), "a\n<content omitted>\nb")
        self.assertEqual(scrub.scrub("a\n    return db.execute('SELECT 1')\nb", CTX), "a\n<content omitted>\nb")
        self.assertEqual(scrub.scrub("a\nx = 1;\ny = 2;\nb", CTX), "a\n<content omitted>\nb")

    def test_a_single_prose_looking_line_is_not_content(self):
        self.assertEqual(scrub.scrub("x = 1 is odd", CTX), "x = 1 is odd")
        self.assertEqual(scrub.scrub("    an indented sentence of prose", CTX), "    an indented sentence of prose")


class N4Leaks(unittest.TestCase):
    def test_nothing_leaks(self):
        for label, raw, secret in LEAKS:
            with self.subTest(label):
                out = scrub.scrub(raw, CTX)
                self.assertNotIn(secret, out, "%r -> %r" % (raw, out))

    def test_real_key_names_with_values_are_still_secrets(self):
        for raw in ("DB_PASSWORD=s3cr3t", "api_key: abcdef123456", "--token=abc123XYZ", "secret = hunter2hunter2"):
            self.assertIn("<secret>", scrub.scrub(raw, CTX), raw)


class GateTest(unittest.TestCase):
    def test_clean_text_passes(self):
        self.assertIsNone(scrub.gate(scrub.scrub(PARA, CTX)))

    def test_residual_high_entropy_blocks(self):
        reason = scrub.gate("failed with 9fK2mQx7LpR4vNb8Zc1TyUe6WsAd3GhJ today in the digest delivery")
        self.assertIn("high-entropy", reason)

    def test_residual_secret_like_blocks(self):
        for text in ("the password: hunter2 was in the digest text and more words", "Authorization: Basic abcdefgh1234 was sent here",
                     "-----BEGIN PRIVATE KEY----- appears in this long enough sentence"):
            self.assertTrue(scrub.gate(text), text)

    def test_too_little_useful_text_blocks(self):
        for text in ("<content omitted>", "<path> <secret>", "x problem", "<content omitted>\n<path>\n-", ""):
            self.assertIn("too little", scrub.gate(text) or "", text)

    def test_placeholders_do_not_count_as_useful(self):
        self.assertTrue(scrub.gate("<path> <host> <secret> <id> <project> <content omitted> ok"))


class GateInFindingsTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.write_config({"machine": {"name": "home", "kind": "home"}, "upstream": {"mode": "auto"}})

    class Gh:
        def __init__(self):
            self.calls = []

        def __call__(self, argv):
            self.calls.append(argv)
            return 0, "https://github.com/alexander-gavrilov/foreman/issues/9\n", ""

    def test_an_empty_scrub_result_stays_a_draft_and_gh_is_not_called(self):
        from foreman import findings
        gh = self.Gh()
        f = findings.report_foreman("```\nonly code\n```", runner=gh)
        self.assertEqual((f["status"], gh.calls), ("draft", []))
        self.assertIn("too little", f["reason"])

    def test_residual_secret_stays_a_draft_with_the_reason(self):
        from foreman import findings
        gh = self.Gh()
        with mock.patch.object(scrub, "scrub", return_value="the digest failed because Zq9Xw3Er7Ty1Ui5Op2As6Df8Gh4Jk0Lm was queued"):
            f = findings.report_foreman("anything", runner=gh)
        self.assertEqual((f["status"], gh.calls), ("draft", []))
        self.assertIn("high-entropy", f["reason"])

    def test_a_plain_long_paragraph_is_filed(self):
        from foreman import findings
        gh = self.Gh()
        f = findings.report_foreman(PARA, runner=gh)
        self.assertEqual(f["status"], "sent")
        self.assertEqual(len(gh.calls), 1)
        argv = gh.calls[0]
        self.assertNotEqual(argv[argv.index("--title") + 1], "foreman finding")
        self.assertIn("did not wake", argv[argv.index("--body") + 1])

    def test_report_output_and_listing_show_the_reason(self):
        import io
        from contextlib import redirect_stdout
        from foreman.cli import main
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["report", "```\nonly code\n```"])
        out = buf.getvalue()
        self.assertIn("saved as draft", out)
        self.assertIn("too little", out)
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["report", "--list"])
        self.assertIn("too little", buf.getvalue())
        from foreman import findings
        self.assertIn("too little", findings.list_findings()[0]["reason"])


if __name__ == "__main__":
    unittest.main()
