"""Review 5 R5-2 / R5-3 (D58): the gate is the safety boundary, the scrubber makes reports readable.
Every R5-2 leak is removed by scrub() AND held by gate() even when scrub() never saw it."""
import unittest

from tests import helpers  # noqa: F401,E402  (puts the foreman package on sys.path)
from foreman import scrub  # noqa: E402

CTX = {"homes": ["/home/alex"], "paths": ["/home/alex/code/shop-app"], "names": ["shop-app"], "hosts": ["alexs-mac"]}

TOKEN_A = "aBcDeFgHiJkLmNoPqRsTuVwXyZaBcDeF"
TOKEN_B = "key_AbCdEfGhIj99_MnOpQrStUv12"

# (label, text around the leak, fragment that must not survive scrub())
LEAKS = [
    ("dot-slash path", "the hook wrote ./acme/secret.py during the wake pass", "acme/secret"),
    ("dot-dot-slash path", "the hook wrote ../acme/secret.py during the wake pass", "acme/secret"),
    ("second path in PATH", "the shell had PATH=/bin:/opt/acme/bin during the hook run", "opt/acme"),
    ("acme.ai", "the worker called acme.ai during the stop hook", "acme.ai"),
    ("linear.app", "the worker called linear.app during the stop hook", "linear.app"),
    ("fly.dev", "the worker called fly.dev during the stop hook", "fly.dev"),
    ("stripe.co", "the worker called stripe.co during the stop hook", "stripe.co"),
    ("cern.ch", "the worker called cern.ch during the stop hook", "cern.ch"),
    ("pl host", "the worker called db01.corp.pl during the stop hook", "corp.pl"),
    ("ip after node", "the daemon reached node 10.24.16.82 during the stop hook", "10.24.16.82"),
    ("ip after orca", "the daemon reached orca 192.168.10.25 during the stop hook", "192.168.10.25"),
    ("ip after herdr", "the daemon reached herdr 10.0.0.7 during the stop hook", "10.0.0.7"),
    ("ip after foreman", "the daemon reached foreman 172.16.4.9 during the stop hook", "172.16.4.9"),
    ("secret is camel", "The secret is CorrectHorseBattery and it showed in the digest", "CorrectHorseBattery"),
    ("api_key is", "The api_key is mySuperSecretKeyValue and it showed in the digest", "mySuperSecretKeyValue"),
    ("password angle", "the digest said password: <hunter2> in the middle of a long sentence", "hunter2"),
    ("alpha-only random", "the call used " + TOKEN_A + " as the credential in the header", TOKEN_A),
    ("segmented random", "the call used " + TOKEN_B + " as the credential in the header", "AbCdEfGhIj99"),
]

# Things the gate must hold even if the scrubber let them through: (label, text)
GATE_ONLY = [
    ("path separator", "see acme/billing in the digest for the failing wake pass"),
    ("relative path", "the file ./notes/todo is where the wake pass wrote the text"),
    ("dotted name", "the worker opened billing.service while the wake pass ran"),
    ("dotted file", "the worker edited secret.py while the wake pass ran in the project"),
    ("host port", "the wake pass could not reach orchestrator:8080 in the project"),
    ("host port 2", "the wake pass could not reach db01:5432 in the project"),
    ("ipv4", "the wake pass could not reach 8.8.4.4 in the project at all"),
    ("ipv6", "the wake pass could not reach 2001:db8::1 in the project at all"),
    ("is secret", "the wake pass logged that the token is Zebra42Quokka in the digest"),
    ("was secret", "the wake pass logged that the password was swordfish in the digest"),
    ("colon secret", "the wake pass logged secret: swordfish in the digest text"),
    ("angle secret", "the wake pass logged api_key = <swordfish> in the digest text"),
    ("random long alpha", "the wake pass used aBcDeFgHiJkLmNoPq in the digest text"),
    ("random segmented", "the wake pass used abc-XyZaBcDe-12 and more " + TOKEN_B + " in the digest"),
]

# R5-3: realistic findings that mention foreman's own paths, URLs and vocabulary must pass the gate
USEFUL = [
    "foreman watch crashes with a KeyError in skills/foreman/lib/foreman/watch.py when the inbox is empty",
    "The wake for a finished worker arrived twice, see https://github.com/alexander-gavrilov/foreman/pull/12 for the fix",
    "After `foreman open` the board shows the session as starting for several minutes although the agent is working.",
    "hooks.py sends {} for an agy PreToolUse and the interactive TUI denies every tool call (see foreman.hooks).",
    "Version 0.4.1 of foreman prints 'terminal gone' for a live terminal after the Orca listing times out.",
    "The digest is empty when two events arrive within one second; expected: both rows, got: none. Happens on macOS and Linux.",
    "Issue alexander-gavrilov/foreman#12 describes the same stop hook; here the Stop output is {\"decision\":\"continue\"} twice.",
    "foreman say is refused for workers with exit 2, which is right, but the message should name the orchestrator role.",
    "The web board and the Telegram topic disagree about who owns a session read/write and and/or stuck state after 24/7 runs.",
    "See skills/foreman/lib/foreman/delivery.py and https://github.com/alexander-gavrilov/foreman/issues/7 about the 45 s grace period.",
    "context_tokens: 150000 is shown as unknown on the status line although the transcript is readable; max_tokens: 4096 too.",
    "Stop hook order: PreInvocation fires, then PreToolUse, then Stop; e.g. the second Stop does not claim the row twice.",
    "The inbox row stays claimed by deliver after the watch typed it, so foreman status reports 1 pending for an hour (3.12.1).",
    "The scrubber turned a plain sentence into <path> twice; the report was too short to file: <path> <path> and nothing else useful here.",
]


class ScrubRemovesTest(unittest.TestCase):
    def test_every_r5_2_leak_is_scrubbed(self):
        for label, text, fragment in LEAKS:
            with self.subTest(label):
                out = scrub.scrub(text, CTX)
                self.assertNotIn(fragment, out, "%r -> %r" % (text, out))


class GateHoldsTest(unittest.TestCase):
    def assertHeld(self, text):
        reason = scrub.gate(text)
        self.assertTrue(reason, text)
        self.assertNotIn("too little", reason, text)

    def test_every_r5_2_leak_is_held_even_unscrubbed(self):
        for label, text, fragment in LEAKS:
            with self.subTest(label):
                self.assertHeld(text)

    def test_gate_only_shapes(self):
        for label, text in GATE_ONLY:
            with self.subTest(label):
                self.assertHeld(text)

    def test_leaks_never_get_through_both_layers(self):
        for label, text, fragment in LEAKS:
            with self.subTest(label):
                scrubbed = scrub.scrub(text, CTX)
                self.assertTrue(fragment not in scrubbed or scrub.gate(scrubbed), scrubbed)


class UsefulReportsPassTest(unittest.TestCase):
    def test_useful_reports_pass_scrub_unchanged_and_the_gate(self):
        self.assertGreaterEqual(len(USEFUL), 10)
        for text in USEFUL:
            with self.subTest(text[:50]):
                out = scrub.scrub(text, CTX)
                self.assertIsNone(scrub.gate(out), "%r -> %r" % (text, out))

    def test_foreman_paths_and_urls_in_particular(self):
        for text in ("see skills/foreman/lib/foreman/watch.py for the pass and the details of the wake",
                     "the repo alexander-gavrilov/foreman has the details of the wake pass in the code",
                     "details at https://github.com/alexander-gavrilov/foreman/pull/12 about the wake pass"):
            self.assertIsNone(scrub.gate(scrub.scrub(text, CTX)), text)

    def test_placeholders_from_the_scrubber_are_fine(self):
        self.assertIsNone(scrub.gate("the worker wrote <path> on <host> using password: <secret> in the wake pass"))
