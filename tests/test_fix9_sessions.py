"""Fix round 9: the session list served to the UI (closed records left out, `live`, the drawer count)."""
import os

from tests.test_hooks import HookCase


class SessionListForTheUiTest(HookCase):
    def test_closed_records_are_left_out_rotated_ones_stay_and_live_matches_the_header(self):
        from foreman.web import state_view
        old = self.sessions.create_session(self.slug, "claude", role="orchestrator", title="old", state="busy")
        new = self.sessions.create_session(self.slug, "claude", role="orchestrator", title="new", state="idle",
                                           replaces=old["sid"])
        self.sessions.update_session(old["sid"], replaced_by=new["sid"])
        self.sessions.set_lifecycle(old["sid"], "rotated")
        for i in range(4):  # closed records that still say "busy"
            c = self.sessions.create_session(self.slug, "claude", title="c%d" % i, state="busy")
            self.sessions.set_lifecycle(c["sid"], "closed")
        gone = self.sessions.create_session(self.slug, "claude", title="vanished", state="gone")
        detail = state_view.build_project(self.slug)
        self.assertEqual(sorted(s["title"] for s in detail["sessions"]), ["new", "old", "vanished"])
        by_title = {s["title"]: s for s in detail["sessions"]}
        self.assertEqual((by_title["old"]["replaced_by"], by_title["new"]["replaces"]), (new["sid"], old["sid"]))
        self.assertEqual({t: s["live"] for t, s in by_title.items()},
                         {"old": False, "new": True, "vanished": False})
        self.assertEqual(sum(1 for s in detail["sessions"] if s["live"]), detail["strip"]["sessions"])
        del gone

    def test_the_ui_counts_live_sessions_and_the_doc_describes_the_list(self):
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
        with open(os.path.join(root, "skills", "foreman", "web", "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("s.live !== false", html)  # the drawer's count and header count live sessions only
        with open(os.path.join(root, "docs", "build", "web-api.md"), encoding="utf-8") as f:
            doc = f.read()
        self.assertIn('"live"', doc)
        self.assertRegex(" ".join(doc.split()), r"closed\W+.{0,40}left out")
