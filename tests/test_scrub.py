import unittest

from tests.helpers import ForemanTestCase
from foreman import scrub  # noqa: E402

CTX = {"homes": ["/home/alex"], "paths": ["/home/alex/code/shop-app", "/Users/bob/work/Acme Billing"],
       "names": ["Shop App", "shop-app", "acme"], "hosts": ["ghome", "bobs-mac"]}

CASES = [
    ("home dir", "cannot read /home/alex/.config/x.json", "cannot read <path>"),
    ("project path", "crash in /home/alex/code/shop-app/src/main.py line", "crash in <path> line"),
    ("project path with space", "see /Users/bob/work/Acme Billing/a.txt now", "see <path> now"),
    ("tilde path", "edit ~/projects/secret/notes.md now", "edit <path> now"),
    ("project name", "the Shop App checkout fails", "the <project> checkout fails"),
    ("project slug", "run in shop-app please", "run in <project> please"),
    ("name case-insensitive", "ACME broke", "<project> broke"),
    ("name not inside word", "acmeish stays", "acmeish stays"),
    ("sid", "session s-1a2b3c died", "session <id> died"),
    ("uuid", "conv 123e4567-e89b-12d3-a456-426614174000 gone", "conv <id> gone"),
    ("hex id", "commit deadbeef12 broke it", "commit <id> broke it"),
    ("short hex kept", "error code abc123 appeared", "error code abc123 appeared"),
    ("email", "mail me at bob.smith@corp-mail.com ok", "mail me at <email> ok"),
    ("machine name", "on ghome the unit failed", "on <host> the unit failed"),
    ("hostname", "ssh bobs-mac refused", "ssh <host> refused"),
    ("lan host", "open http-less ghome.lan now", "open http-less <host> now"),
    ("url", "see https://example.com/a?b=1 for more", "see <url> for more"),
    ("url trailing dot", "go to https://example.com/x.", "go to <url>."),
    ("allowed url", "dup of https://github.com/alexander-gavrilov/foreman/issues/3 ok",
     "dup of https://github.com/alexander-gavrilov/foreman/issues/3 ok"),
    ("other github url", "see https://github.com/someone/shop/issues/1", "see <url>"),
    ("fenced block", "before\n```python\nprint(1)\n```\nafter", "before\n<content omitted>\nafter"),
    ("indented code", "failed:\n    x = secret()\n    y = 2\nend", "failed:\n<content omitted>\nend"),
    ("def line", "text\ndef run(a):\nmore", "text\n<content omitted>\nmore"),
    ("json line", 'cfg:\n"token": "abc"\ndone', "cfg:\n<content omitted>\ndone"),
    ("traceback line", 'boom\n  File "x.py", line 3, in f\nend', "boom\n<content omitted>\nend"),
    ("plain prose kept", "The hook exits 0 but the board stays empty.", "The hook exits 0 but the board stays empty."),
    ("product name kept", "foreman init fails", "foreman init fails"),
    ("email before host", "x@ghome.lan wrote", "<email> wrote"),
    ("path inside url", "http://ghome:8765/p/home/alex/x", "<url>"),
    ("many", "s-aaaaaa and s-bbbbbb", "<id> and <id>"),
]


class ScrubTable(unittest.TestCase):
    def test_table(self):
        self.assertGreaterEqual(len(CASES), 20)
        for label, raw, want in CASES:
            if want is None:
                continue
            with self.subTest(label):
                self.assertEqual(scrub.scrub(raw, CTX), want)

    def test_project_named_like_placeholder_does_not_corrupt(self):
        ctx = dict(CTX, names=["id", "path"])
        out = scrub.scrub("abcdef0123456789 in /home/alex/x", ctx)
        self.assertEqual(out, "<id> in <path>")

    def test_foreman_named_project_keeps_product_name(self):
        ctx = dict(CTX, names=["foreman"])
        self.assertEqual(scrub.scrub("foreman is slow", ctx), "foreman is slow")


class ScrubDefaultContext(ForemanTestCase):
    def test_registered_projects_and_home(self):
        proj = self.init_project("secret-ledger")
        self.write_config({"machine": {"name": "workmac", "kind": "work"}})
        raw = "In %s/src/a.py the Secret-Ledger tool on workmac wrote to %s/x" % (proj["path"], self.home)
        out = scrub.scrub(raw)
        for leak in (proj["path"], "ledger", "workmac", self.home):
            self.assertNotIn(leak, out.lower())
        self.assertIn("<path>", out)


if __name__ == "__main__":
    unittest.main()
