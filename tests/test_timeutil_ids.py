import re
import unittest
from datetime import datetime, timezone

from tests.helpers import ForemanTestCase


class TimeUtilTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import timeutil
        self.t = timeutil

    def test_now_iso_format(self):
        self.assertRegex(self.t.now_iso(), r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_foreman_now_override(self):
        import os
        os.environ["FOREMAN_NOW"] = "2026-01-02T03:04:05Z"
        self.assertEqual(self.t.now_iso(), "2026-01-02T03:04:05Z")

    def test_parse(self):
        d = self.t.parse("2026-11-05T12:00:00Z")
        self.assertEqual(d, datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc))
        self.assertIsNone(self.t.parse(None))
        self.assertIsNone(self.t.parse("nonsense"))

    def test_age_seconds(self):
        now = self.t.parse("2026-11-05T12:10:00Z")
        self.assertEqual(self.t.age_seconds("2026-11-05T12:00:00Z", now=now), 600)
        self.assertIsNone(self.t.age_seconds(None, now=now))

    def test_humanize(self):
        h = self.t.humanize
        self.assertEqual(h(0), "0s")
        self.assertEqual(h(45), "45s")
        self.assertEqual(h(60), "1m")
        self.assertEqual(h(4 * 60 + 30), "4m")
        self.assertEqual(h(3 * 3600 + 10), "3h")
        self.assertEqual(h(2 * 86400 + 5), "2d")
        self.assertEqual(h(-5), "0s")
        self.assertEqual(h(None), "?")

    def test_age_text(self):
        now = self.t.parse("2026-11-05T15:00:00Z")
        self.assertEqual(self.t.age_text("2026-11-05T12:00:00Z", now=now), "3h")
        self.assertEqual(self.t.age_text(None, now=now), "?")


class IdsTest(ForemanTestCase):
    def test_sid(self):
        from foreman import ids
        sid = ids.new_sid()
        self.assertRegex(sid, r"^s-[0-9a-f]{6}$")
        self.assertNotEqual(sid, ids.new_sid())

    def test_sid_avoids_existing(self):
        from foreman import ids
        calls = iter(["s-aaaaaa", "s-bbbbbb"])
        got = ids.new_sid(exists=lambda s: s == "s-aaaaaa", _gen=lambda: next(calls))
        self.assertEqual(got, "s-bbbbbb")

    def test_msg_id(self):
        from foreman import ids
        self.assertRegex(ids.new_msg_id(), r"^m-[0-9a-f]{8}$")
        self.assertNotEqual(ids.new_msg_id(), ids.new_msg_id())


if __name__ == "__main__":
    unittest.main()
