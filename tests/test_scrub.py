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


# F4 (review 3c): context of the review ({home /Users/alex, project /Users/alex/work/acme-billing})
CTX_R = {"homes": ["/Users/alex"], "paths": ["/Users/alex/work/acme-billing"], "names": ["acme-billing"],
         "hosts": ["alexs-mac"]}
SECRET_TOKEN = "123456789:AAH" + "x" * 32
LEAKS = [
    # absolute paths outside home / registered projects
    ("posix path elsewhere", "failed in /Volumes/Work/acme/billing-svc/src/payments.py now", "billing-svc"),
    ("other user's home", "see /Users/jdoe/code/app/main.py", "jdoe"),
    ("system-ish path", "cannot open /srv/data/customers/export.csv", "customers"),
    ("windows drive path", r"opened C:\Users\jdoe\Projects\acme\billing.cs ok", "jdoe"),
    ("windows forward slashes", "opened D:/work/acme/billing.cs ok", "acme"),
    ("unc path", r"copied to \\fileserver\share\acme\q3.xlsx", "fileserver"),
    # git remotes and repo slugs
    ("ssh remote", "origin is git@github.com:acmecorp/billing-service.git today", "acmecorp"),
    ("ssh remote host only", "origin is deploy@git.acme.io:team/app", "team"),
    ("owner/repo slug", "fork of acmecorp/billing-service broke", "billing-service"),
    ("relative path", "edit services/billing/payments.py then", "payments"),
    ("two-segment file path", "edit src/payments.py then", "payments"),
    # addresses
    ("ipv4", "db is 10.20.30.40 and 192.168.1.5", "10.20"),
    ("ipv4 second", "db is 10.20.30.40 and 192.168.1.5", "192.168"),
    ("ipv6 compressed", "listen on fe80::1ff:fe23:4567:890a now", "fe80"),
    ("ipv6 full", "peer 2001:0db8:85a3:0000:0000:8a2e:0370:7334 up", "2001"),
    ("fqdn .io", "host db01.acmecorp.io is down", "acmecorp"),
    ("fqdn .com", "ticket on jira.acme.com open", "jira.acme"),
    ("fqdn .co.uk", "wiki.acme.co.uk slow", "acme"),
    # secrets
    ("anthropic key", "key sk-ant-api03-AbCdEf0123456789_-AbCdEf0123456789 leaked", "sk-ant"),
    ("openai-style key", "key sk-proj1234567890abcdefghijkl leaked", "sk-proj"),
    ("aws key id", "id AKIAIOSFODNN7EXAMPLE leaked", "AKIA"),
    ("github token", "tok ghp_0123456789abcdefghijklmnopqrstuvwxyz01 leaked", "ghp_"),
    ("github pat", "tok github_pat_11ABCDEFG0abcdefghijklmnop_qrstuvwxyz leaked", "github_pat"),
    ("telegram token", "bot " + SECRET_TOKEN + " leaked", "AAH"),
    ("slack token", "tok xoxb-1234567890-abcdefghijkl leaked", "xoxb"),
    ("jwt", "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456 leaked", "eyJ"),
    ("bearer", "sent Authorization: Bearer abcDEF123456xyz789 ok", "abcDEF"),
    ("env secret mid-line", "set DB_PASSWORD=s3cr3t and retry", "s3cr3t"),
    ("colon secret", "config api_key: hunter2hunter2 used", "hunter2"),
    ("token equals", "run with --token=abc123XYZ now", "abc123XYZ"),
    # allowed URL passthrough must not smuggle project data
    ("allowed url with query", "see https://github.com/alexander-gavrilov/foreman/issues/1?ref=acme-billing&p=/Users/alex/work/acme-billing",
     "acme-billing"),
    ("allowed url with dotdot", "see https://github.com/alexander-gavrilov/foreman/../../acmecorp/billing-service", "acmecorp"),
    ("allowed url with fragment", "see https://github.com/alexander-gavrilov/foreman/issues/1#acme-billing", "acme-billing"),
    ("allowed url wrong section", "see https://github.com/alexander-gavrilov/foreman/settings/secrets/acme", "acme"),
]

KEPT = [
    ("allowed issue url", "dup of https://github.com/alexander-gavrilov/foreman/issues/3 ok"),
    ("allowed pull url", "fixed by https://github.com/alexander-gavrilov/foreman/pull/12."),
    ("allowed blob url", "see https://github.com/alexander-gavrilov/foreman/blob/main/skills/foreman/SKILL.md ok"),
    ("allowed repo url", "repo https://github.com/alexander-gavrilov/foreman ok"),
    ("repo slug of the product", "install alexander-gavrilov/foreman first"),
    ("plain prose", "The hook exits 0 but the board stays empty (3/4 done, read/write ok)."),
    ("time and version", "at 12:30:45 the v1.2.3 build and 1.5 s delay"),
    ("file name alone", "hooks.json and sessions.py are fine"),
    ("code scope", "std::string and e.g. this"),
    ("github.com bare", "pushed to github.com yesterday"),
]


class ScrubLeaks(unittest.TestCase):
    def test_nothing_leaks(self):
        for label, raw, secret in LEAKS:
            with self.subTest(label):
                out = scrub.scrub(raw, CTX_R)
                self.assertNotIn(secret, out, "%r -> %r" % (raw, out))

    def test_harmless_text_survives(self):
        for label, raw in KEPT:
            with self.subTest(label):
                self.assertEqual(scrub.scrub(raw, CTX_R), raw)

    def test_review_examples_exact(self):
        cases = {
            "git@github.com:acmecorp/billing-service.git": "<repo>",
            "/Volumes/Work/acme/billing-svc/src/payments.py": "<path>",
            "10.20.30.40": "<host>",
            "DB_PASSWORD=s3cr3t": "<secret>",
            "https://github.com/alexander-gavrilov/foreman/issues/1?ref=acme-billing": "<url>",
        }
        for raw, want in cases.items():
            self.assertEqual(scrub.scrub("x " + raw + " y", CTX_R), "x %s y" % want, raw)


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
