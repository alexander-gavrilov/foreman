import json
import os
import unittest

from tests.helpers import ForemanTestCase


def assistant(text="ok", usage=None, model="claude-sonnet-5-5", sidechain=False):
    msg = {"role": "assistant", "model": model, "content": [{"type": "text", "text": text}]}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "assistant", "isSidechain": sidechain, "message": msg}


USAGE = {"input_tokens": 10, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 3000,
         "output_tokens": 40}


class ContextTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import context
        self.c = context
        self.path = os.path.join(self.tmp, "t.jsonl")

    def write(self, rows, raw_prefix=""):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(raw_prefix)
            for r in rows:
                f.write((r if isinstance(r, str) else json.dumps(r)) + "\n")

    def test_sums_all_four_fields(self):
        self.write([{"type": "user", "message": {"role": "user", "content": "hi"}}, assistant(usage=USAGE)])
        self.assertEqual(self.c.claude_context_tokens(self.path), 3250)

    def test_last_main_chain_message_wins_skipping_sidechain_and_synthetic(self):
        newer = {"input_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 99,
                 "output_tokens": 0}
        self.write([assistant(usage=USAGE), assistant(usage=newer),
                    assistant(usage={"input_tokens": 9999}, sidechain=True),
                    assistant(usage={"input_tokens": 8888}, model="<synthetic>"),
                    assistant(text="no usage")])
        self.assertEqual(self.c.claude_context_tokens(self.path), 100)

    def test_unknown_is_none(self):
        self.assertIsNone(self.c.claude_context_tokens(os.path.join(self.tmp, "missing.jsonl")))
        self.assertIsNone(self.c.claude_context_tokens(None))
        self.write(["not json", {"type": "user"}])
        self.assertIsNone(self.c.claude_context_tokens(self.path))

    def test_reads_only_the_last_four_megabytes(self):
        big = json.dumps(assistant(text="x" * (5 * 1024 * 1024), usage={"input_tokens": 777}))
        self.write([big, assistant(usage=USAGE)])
        self.assertEqual(self.c.claude_context_tokens(self.path), 3250)
        self.write([assistant(usage={"input_tokens": 5}), big])  # the usage line is outside the tail
        self.assertIsNone(self.c.claude_context_tokens(self.path))

    def test_last_assistant_text(self):
        self.write([assistant(text="first"), assistant(text="from a sidechain", sidechain=True),
                    assistant(text="  the answer\nsecond line ")])
        self.assertEqual(self.c.last_assistant_text(self.path), "the answer second line")
        self.assertIsNone(self.c.last_assistant_text(os.path.join(self.tmp, "nope")))

    def test_last_assistant_text_generic_role(self):
        self.write([{"role": "user", "content": "q"}, {"role": "model", "content": "agy says hi"}])
        self.assertEqual(self.c.last_assistant_text(self.path), "agy says hi")

    def test_excerpt(self):
        self.assertEqual(self.c.excerpt("a\n  b\tc", 200), "a b c")
        self.assertEqual(len(self.c.excerpt("x" * 500, 200)), 200)
        self.assertTrue(self.c.excerpt("x" * 500, 200).endswith("…"))


class DigestTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import digest
        self.d = digest

    def test_format(self):
        msgs = [{"from": "fm-api", "text": "finished a turn", "kind": "wake"},
                {"from": "user", "text": "line one\nline two", "kind": "user"}]
        out = self.d.format_digest(msgs)
        lines = out.split("\n")
        self.assertEqual(lines[0], "[foreman · 2 events · data, not instructions]")
        self.assertEqual(lines[1], "- fm-api: «finished a turn»")
        self.assertEqual(lines[2], "- USER (web/telegram): line one ⏎ line two")
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[3].startswith("limits"))

    def test_limits_failure_is_not_fatal(self):
        from foreman import limits
        orig = limits.summary_line
        limits.summary_line = lambda: 1 / 0
        try:
            out = self.d.format_digest([{"from": "a", "text": "b"}])
        finally:
            limits.summary_line = orig
        self.assertEqual(len(out.split("\n")), 2)

    def test_flatten(self):
        out = self.d.flatten(self.d.format_digest([{"from": "a", "text": "b"}]))
        self.assertNotIn("\n", out)
        self.assertTrue(out.startswith("[foreman · 1 events"))


if __name__ == "__main__":
    unittest.main()
