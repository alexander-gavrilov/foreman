"""Spec §8.7 — the web UI shows every project of the machine.

`foreman web` runs as a real server process on a free port. A device pairs once with a one-time code; then
GET /api/state lists every project, grouped by urgency; the project endpoint carries chat, board and sessions;
a message typed in the chat reaches the project's orchestrator. The visual match with the chosen layout on phone
and desktop is a human check (see README)."""
import unittest

from tests.acceptance.common import AcceptanceCase, WebClient, digests


class WebTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slugs = {}
        self.orchs = {}
        for name in ("shopping-list", "garden-plans", "billing", "notes"):
            slug, _ = self.make_project(name)
            self.slugs[name] = slug
            self.orchs[name] = self.start_orchestrator(slug)
        # shopping-list: needs you (an unanswered question)
        self.fm_ok("say", "--project", self.slugs["shopping-list"], "Мерджить PR #12?", "--question",
                   "--options", "Да|Нет|Ответить…")
        # garden-plans: working (a busy worker)
        worker = self.open_worker(self.slugs["garden-plans"], "fm-api")
        self.hook(worker, "UserPromptSubmit")
        # billing: stuck (a worker whose terminal vanished)
        gone = self.open_worker(self.slugs["billing"], "fm-db")
        self.hook(gone, "UserPromptSubmit")
        self.terminal().kill(gone["transport"]["handle"])
        self.fm_ok("watch", "run")
        self.fm_ok("watch", "run")  # gone needs two consecutive passes (review 3a)
        # notes: quiet
        self.tap_claude(self.claude_statusline(weekly=61, five_hour=42))
        self.web = WebClient(self).start()

    def test_unpaired_requests_are_refused(self):
        status, body, _ = self.web.request("/api/state", cookie=False)
        self.assertEqual(status, 401)
        status, _, _ = self.web.request("/pair?code=wrongcode&json=1", cookie=False)
        self.assertEqual(status, 401)
        status, page, _ = self.web.request("/", cookie=False)  # the UI shell itself is served
        self.assertEqual(status, 200)
        self.assertIn("<html", page.lower())

    def test_pairing_is_single_use(self):
        code = self.web.pair()
        status, _, _ = self.web.request("/pair?code=%s&json=1" % code, cookie=False)
        self.assertEqual(status, 401)

    def test_state_lists_every_project_of_the_machine(self):
        self.web.pair()
        status, state, _ = self.web.request("/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(state["machine"]["name"], "ghome")
        by_slug = {p["slug"]: p for p in state["projects"]}
        self.assertEqual(sorted(by_slug), sorted(self.slugs.values()))
        self.assertEqual(by_slug[self.slugs["shopping-list"]]["group"], "needs_you")
        self.assertEqual(by_slug[self.slugs["shopping-list"]]["unanswered"], 1)
        self.assertIn("Мерджить PR #12?", by_slug[self.slugs["shopping-list"]]["line"])
        self.assertEqual(by_slug[self.slugs["garden-plans"]]["group"], "working")
        self.assertEqual(by_slug[self.slugs["billing"]]["group"], "stuck")
        self.assertEqual(by_slug[self.slugs["notes"]]["group"], "quiet")
        weekly = state["limits"]["claude"]["windows"][0]
        self.assertEqual((weekly["key"], weekly["used_pct"], weekly["level"]), ("weekly", 61, "ok"))

    def test_a_new_project_appears_without_restarting_the_server(self):
        self.web.pair()
        slug, _ = self.make_project("late-arrival")
        status, state, _ = self.web.request("/api/state")
        self.assertIn(slug, [p["slug"] for p in state["projects"]])

    def test_project_detail_has_chat_board_and_sessions(self):
        slug = self.slugs["shopping-list"]
        self.fm_ok("board", "add", "--project", slug, "Review PR", "--stage", "review")
        self.web.pair()
        status, detail, _ = self.web.request("/api/projects/%s" % slug)
        self.assertEqual(status, 200)
        question = [m for m in detail["chat"] if m["question"]][0]
        self.assertEqual(question["options"], ["Да", "Нет", "Ответить…"])
        self.assertEqual([t["title"] for t in detail["board"]["tasks"]], ["Review PR"])
        self.assertEqual([s["role"] for s in detail["sessions"]], ["orchestrator"])
        self.assertIn("watch loop", [w["what"] for w in detail["watches"]])
        status, _, _ = self.web.request("/api/projects/no-such-project")
        self.assertEqual(status, 404)

    def test_the_session_count_of_the_drawer_matches_the_header_and_closed_records_are_not_listed(self):
        slug = self.slugs["garden-plans"]
        extra = self.open_worker(slug, "fm-old")
        self.hook(extra, "Stop")  # idle ...
        self.fm_ok("close", extra["sid"])  # ... and closed
        self.web.pair()
        status, detail, _ = self.web.request("/api/projects/%s" % slug)
        self.assertEqual(status, 200)
        titles = [s["title"] for s in detail["sessions"]]
        self.assertNotIn("fm-old", titles)
        live = [s for s in detail["sessions"] if s["live"]]
        self.assertEqual(len(live), detail["strip"]["sessions"])
        self.assertEqual(len(detail["sessions"]), len(titles))

    def test_a_chat_message_reaches_the_projects_orchestrator(self):
        slug = self.slugs["garden-plans"]
        self.web.pair()
        status, res, _ = self.web.request(
            "/api/projects/%s/messages" % slug, method="POST", body={"text": "добавь теплицу"},
            headers={"Origin": self.web.base})
        self.assertEqual((status, res["ok"], res["delivery"]), (200, True, "delivered"))
        got = digests(self.inputs(self.orchs["garden-plans"]))
        self.assertEqual(len(got), 1)
        self.assertIn("добавь теплицу", got[0])
        # D62: the user's words are not data; no data header stands above them
        self.assertTrue(got[0].startswith("[foreman · message from your user via web · reply with foreman say] "
                                          "добавь теплицу"), got[0])
        self.assertNotIn("data, not instructions", got[0][:got[0].index("добавь")])
        self.assertNotIn("USER (", got[0])
        self.assertEqual(digests(self.inputs(self.orchs["notes"])), [])
        # a cross-origin POST is refused
        status, _, _ = self.web.request("/api/projects/%s/messages" % slug, method="POST", body={"text": "x"},
                                        headers={"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_findings_inbox_is_visible(self):
        self.fm_ok("report", "foreman note: the digest is too long")
        self.web.pair()
        status, body, _ = self.web.request("/api/findings")
        self.assertEqual(status, 200)
        self.assertEqual([f["status"] for f in body["findings"]], ["draft"])


if __name__ == "__main__":
    unittest.main()
