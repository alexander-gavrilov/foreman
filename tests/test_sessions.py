import os
import unittest

from tests.helpers import ForemanTestCase


class SessionsTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import sessions
        self.s = sessions

    def test_create_defaults(self):
        rec = self.s.create_session("demo", "claude", model="sonnet", title="fm-api", cwd="/x",
                                    transport={"kind": "fake", "handle": "h1"})
        self.assertRegex(rec["sid"], r"^s-[0-9a-f]{6}$")
        self.assertEqual(rec["project"], "demo")
        self.assertEqual(rec["role"], "worker")
        self.assertEqual(rec["owner"], "orchestrator")
        self.assertEqual(rec["state"], "starting")
        self.assertIsNone(rec["context_tokens"])
        self.assertIsNone(rec["replaced_by"])
        self.assertIsNone(rec["closed_at"])
        self.assertTrue(rec["opened_at"].endswith("Z"))
        self.assertTrue(rec["state_since"].endswith("Z"))
        self.assertEqual(self.s.load_session(rec["sid"]), rec)
        for key in ("sid", "project", "agent", "model", "effort", "role", "owner", "purpose", "title",
                    "transport", "agent_session_id", "cwd", "transcript_path", "state", "state_since",
                    "last_event_at", "context_tokens", "context_at", "replaces", "replaced_by",
                    "opened_at", "closed_at"):
            self.assertIn(key, rec)

    def test_load_missing(self):
        self.assertIsNone(self.s.load_session("s-000000"))

    def test_list_by_project(self):
        a = self.s.create_session("p1", "claude", title="a")
        self.s.create_session("p2", "codex", title="b")
        self.assertEqual(len(self.s.list_sessions()), 2)
        self.assertEqual([r["sid"] for r in self.s.list_sessions("p1")], [a["sid"]])

    def test_list_sorted_by_opened_at_then_sid(self):
        os.environ["FOREMAN_NOW"] = "2026-01-01T00:00:00Z"
        a = self.s.create_session("p", "claude", title="a")
        os.environ["FOREMAN_NOW"] = "2026-01-01T00:00:05Z"
        b = self.s.create_session("p", "claude", title="b")
        self.assertEqual([r["sid"] for r in self.s.list_sessions("p")], [a["sid"], b["sid"]])

    def test_find_by_all_keys(self):
        rec = self.s.create_session("p", "claude", title="fm-api", agent_session_id="uuid-1",
                                    transport={"kind": "fake", "handle": "term_9"})
        sid = rec["sid"]
        self.assertEqual(self.s.find(sid)["sid"], sid)
        self.assertEqual(self.s.find("fm-api")["sid"], sid)
        self.assertEqual(self.s.find("uuid-1")["sid"], sid)
        self.assertEqual(self.s.find("term_9")["sid"], sid)
        self.assertIsNone(self.s.find("unknown"))

    def test_find_by_handle_requires_kind_match_when_given(self):
        rec = self.s.create_session("p", "claude", transport={"kind": "fake", "handle": "h"})
        self.assertEqual(self.s.find_by_handle("h", kind="fake")["sid"], rec["sid"])
        self.assertIsNone(self.s.find_by_handle("h", kind="orca"))

    def test_find_title_prefers_live_session_and_project_scope(self):
        old = self.s.create_session("p", "claude", title="dup")
        self.s.set_state(old["sid"], "closed")
        new = self.s.create_session("p", "claude", title="dup")
        other = self.s.create_session("q", "claude", title="dup")
        self.assertEqual(self.s.find("dup", project="p")["sid"], new["sid"])
        self.assertEqual(self.s.find("dup", project="q")["sid"], other["sid"])

    def test_set_state_tracks_state_since_only_on_change(self):
        os.environ["FOREMAN_NOW"] = "2026-01-01T00:00:00Z"
        rec = self.s.create_session("p", "claude")
        os.environ["FOREMAN_NOW"] = "2026-01-01T00:10:00Z"
        r1 = self.s.set_state(rec["sid"], "busy")
        self.assertEqual(r1["state"], "busy")
        self.assertEqual(r1["state_since"], "2026-01-01T00:10:00Z")
        os.environ["FOREMAN_NOW"] = "2026-01-01T00:20:00Z"
        r2 = self.s.set_state(rec["sid"], "busy")
        self.assertEqual(r2["state_since"], "2026-01-01T00:10:00Z")
        r3 = self.s.set_state(rec["sid"], "idle")
        self.assertEqual(r3["state_since"], "2026-01-01T00:20:00Z")
        self.assertEqual(self.s.load_session(rec["sid"])["state"], "idle")

    def test_set_state_closed_sets_closed_at_and_rejects_unknown_state(self):
        rec = self.s.create_session("p", "claude")
        self.assertTrue(self.s.set_state(rec["sid"], "closed")["closed_at"])
        with self.assertRaises(ValueError):
            self.s.set_state(rec["sid"], "weird")
        with self.assertRaises(KeyError):
            self.s.set_state("s-ffffff", "idle")

    def test_update_fields(self):
        rec = self.s.create_session("p", "claude")
        out = self.s.update_session(rec["sid"], context_tokens=1234, model="opus")
        self.assertEqual(out["context_tokens"], 1234)
        self.assertEqual(self.s.load_session(rec["sid"])["model"], "opus")

    def test_inbox_flow(self):
        rec = self.s.create_session("p", "claude")
        sid = rec["sid"]
        self.assertEqual(self.s.inbox_pending(sid), [])
        m1 = self.s.inbox_enqueue(sid, "first", sender="orchestrator")
        m2 = self.s.inbox_enqueue(sid, "second", sender="user", kind="note")
        self.assertRegex(m1["id"], r"^m-[0-9a-f]{8}$")
        self.assertEqual(m2["kind"], "note")
        self.assertIsNone(m1["delivered_at"])
        self.assertEqual([m["text"] for m in self.s.inbox_pending(sid)], ["first", "second"])
        self.s.inbox_mark_delivered(sid, [m1["id"]])
        self.assertEqual([m["text"] for m in self.s.inbox_pending(sid)], ["second"])
        self.assertEqual(len(self.s.inbox_all(sid)), 2)
        self.assertTrue([m for m in self.s.inbox_all(sid) if m["id"] == m1["id"]][0]["delivered_at"])
        self.s.inbox_mark_delivered(sid)  # all
        self.assertEqual(self.s.inbox_pending(sid), [])

    def test_inbox_claim_is_exclusive_and_stamps(self):
        rec = self.s.create_session("p", "claude")
        sid = rec["sid"]
        self.assertEqual(self.s.inbox_claim(sid), [])
        a = self.s.inbox_enqueue(sid, "a")
        b = self.s.inbox_enqueue(sid, "b")
        got = self.s.inbox_claim(sid, claimer="deliver")
        self.assertEqual([m["id"] for m in got], [a["id"], b["id"]])
        self.assertTrue(all(m["delivered_at"] for m in got))
        self.assertEqual(got[0]["claimed_by"], "deliver")
        self.assertEqual(self.s.inbox_claim(sid), [])        # a second claim gets nothing
        self.assertEqual(self.s.inbox_pending(sid), [])       # and pending (read-only) is empty
        c = self.s.inbox_enqueue(sid, "c")
        self.assertEqual([m["id"] for m in self.s.inbox_claim(sid)], [c["id"]])

    def test_inbox_claim_limit_and_pending_is_read_only(self):
        sid = self.s.create_session("p", "claude")["sid"]
        for t in "abc":
            self.s.inbox_enqueue(sid, t)
        self.assertEqual(len(self.s.inbox_pending(sid)), 3)
        self.assertEqual(len(self.s.inbox_pending(sid)), 3)   # looking does not consume
        self.assertEqual([m["text"] for m in self.s.inbox_claim(sid, limit=2)], ["a", "b"])
        self.assertEqual([m["text"] for m in self.s.inbox_pending(sid)], ["c"])

    def test_inbox_release_returns_failed_deliveries_to_pending(self):
        sid = self.s.create_session("p", "claude")["sid"]
        self.s.inbox_enqueue(sid, "a")
        got = self.s.inbox_claim(sid)
        self.s.inbox_release(sid, [m["id"] for m in got])
        self.assertEqual([m["text"] for m in self.s.inbox_pending(sid)], ["a"])
        self.assertEqual(len(self.s.inbox_claim(sid)), 1)

    def test_inbox_claim_across_processes_delivers_each_message_once(self):
        from tests import mp_workers
        sid = self.s.create_session("p", "claude")["sid"]
        sent = [self.s.inbox_enqueue(sid, "m%d" % i)["id"] for i in range(60)]
        results = mp_workers.run_in_processes(mp_workers.claim_all, [(sid, 3)] * 5)
        claimed = [i for part in results for i in part]
        self.assertEqual(len(claimed), 60)
        self.assertEqual(sorted(claimed), sorted(sent))       # no duplicates, none lost

    def test_find_title_ambiguous_across_projects(self):
        a = self.s.create_session("p1", "claude", title="api")
        b = self.s.create_session("p2", "claude", title="api")
        with self.assertRaises(self.s.AmbiguousSession) as cm:
            self.s.find("api")
        msg = str(cm.exception)
        for part in (a["sid"], b["sid"], "p1", "p2"):
            self.assertIn(part, msg)
        # scoped to a project: unambiguous
        self.assertEqual(self.s.find("api", project="p2")["sid"], b["sid"])

    def test_find_title_is_scoped_to_given_project_only(self):
        self.s.create_session("p1", "claude", title="only-here")
        self.assertIsNone(self.s.find("only-here", project="p2"))
        self.assertIsNotNone(self.s.find("only-here"))   # unique overall -> found

    def test_find_ignores_inactive_duplicates_for_ambiguity(self):
        a = self.s.create_session("p1", "claude", title="api")
        old = self.s.create_session("p2", "claude", title="api")
        self.s.set_state(old["sid"], "closed")
        self.assertEqual(self.s.find("api")["sid"], a["sid"])

    def test_find_sid_and_handle_are_global_even_with_project(self):
        a = self.s.create_session("p1", "claude", title="x", transport={"kind": "fake", "handle": "H"})
        self.assertEqual(self.s.find(a["sid"], project="p2")["sid"], a["sid"])
        self.assertEqual(self.s.find("H", project="p2")["sid"], a["sid"])

    def test_corrupt_session_file_is_skipped_in_listing(self):
        from foreman import paths
        good = self.s.create_session("p", "claude", title="good")
        bad = self.s.create_session("p", "claude", title="bad")
        with open(paths.session_file(bad["sid"]), "w") as f:
            f.write("{")
        self.assertEqual([r["sid"] for r in self.s.list_sessions("p")], [good["sid"]])


if __name__ == "__main__":
    unittest.main()
