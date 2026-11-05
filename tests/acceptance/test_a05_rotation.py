"""Spec §8.5 — a rotation keeps the work going: the new session has the handoff as a message; one batched question
to the old session, inside the warm-cache window, is delivered and its answer comes back; the board shows who
replaced whom. Nobody is interrupted and the old session is never closed."""
import unittest

from tests.acceptance.common import AcceptanceCase, digests


class RotationTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.slug, self.path = self.make_project()
        self.orch = self.start_orchestrator(self.slug)
        self.old = self.open_worker(self.slug, "fm-api")
        self.hook(self.old, "UserPromptSubmit")
        self.hook(self.old, "Stop", extra={"last_assistant_message": "half way"})
        self.fm_ok("board", "add", "--project", self.slug, "API", "--stage", "build", "--owner", "fm-api")

    def rotate(self):
        out = self.fm_json("rotate", "--project", self.slug, "fm-api", "--transport", "fake", "--json")
        return out["old"], out["new"]

    def as_session(self, rec):
        return {"FOREMAN_SESSION": rec["sid"]}

    def test_full_rotation(self):
        old, new = self.rotate()
        self.assertEqual((old["lifecycle"], old["replaced_by"], new["replaces"]), ("rotated", new["sid"], old["sid"]))
        self.assertNotEqual(old["transport"]["handle"], new["transport"]["handle"])

        # the old session is asked (in its own terminal) to write the handoff and deliver it by message
        old_inputs = self.inputs(old)
        self.assertEqual(len(digests(old_inputs)), 1)
        self.assertIn("deliver it directly with `foreman send %s -`" % new["sid"], old_inputs[-1])
        # the new session was launched with the rotation brief: wait for the handoff, batch questions
        brief = self.inputs(new)[0]
        self.assertIn("You replace session %s" % old["sid"], brief)
        self.assertIn("ONE `foreman ask", brief)

        # (1) the handoff arrives as a direct message, typed into the new terminal, not as a file
        handoff = "Done: schema + endpoints. Left: pagination. Open question: cursor vs offset."
        self.fm_ok("send", new["sid"], "-", stdin=handoff, env=self.as_session(old))
        got = digests(self.inputs(new))
        self.assertEqual(len(got), 1)
        self.assertIn(handoff, got[0])
        self.assertIn("fm-api", got[0])  # from the old session (sender = its title)

        # (2) inside the warm window the new session batches ALL its questions into ONE ask
        self.advance(minutes=20)
        code, out, err = self.fm("ask", old["sid"], "1) why cursor? 2) which tests are flaky? 3) where is the seed?",
                                 env=self.as_session(new))
        self.assertEqual(code, 0, err)
        self.assertNotIn("cold", err, "within the warm window there is no cold-cache warning")
        msgid = out.strip()
        asked = digests(self.inputs(old))
        self.assertEqual(len(asked), 2)  # the handoff request + the batched question
        self.assertIn("why cursor", asked[-1])
        self.assertIn("foreman reply %s" % msgid, asked[-1])

        # (3) the old session answers; the answer is routed back into the new session's terminal
        self.fm_ok("reply", msgid, "1) offset is simpler 2) none 3) tests/seed.sql", env=self.as_session(old))
        answers = [t for t in digests(self.inputs(new)) if "answer to your question" in t]
        self.assertEqual(len(answers), 1)
        self.assertIn("tests/seed.sql", answers[0])

        # (4) the board and status show old -> new; the old session is rotated, not lost or closed
        board = self.fm_ok("board", "show", "--project", self.slug)
        self.assertIn("fm-api → %s" % new["title"], board)
        status = self.fm_ok("status", "--project", self.slug)
        self.assertRegex(status, r"fm-api\s+%s\s+.*rotated → %s" % (old["sid"], new["sid"]))
        self.assertIn("rotated %s → %s" % (old["sid"], new["sid"]), self.fm_ok("log", "search", "--project", self.slug, "rotated"))

        # (5) the watch loop and the clock never close or wind down the rotated session
        self.advance(hours=3)
        self.fm_ok("watch", "run")
        still = self.session(old["sid"])
        self.assertEqual((still["lifecycle"], still["state"]), ("rotated", "idle"))
        self.assert_no_model_started()

    def test_a_question_after_the_warm_window_warns_that_the_cache_is_cold(self):
        old, new = self.rotate()
        self.advance(hours=2)
        code, out, err = self.fm("ask", old["sid"], "why?", env=self.as_session(new))
        self.assertEqual(code, 0, err)
        self.assertIn("cache is cold", err)

    def test_a_busy_session_is_not_rotated(self):
        self.hook(self.old, "UserPromptSubmit")
        code, out, err = self.fm("rotate", "--project", self.slug, "fm-api", "--transport", "fake")
        self.assertEqual(code, 6)
        self.assertIn("busy", err)
        self.assertEqual(self.session(self.old["sid"])["lifecycle"], "active")

    def test_rotating_the_orchestrator_hands_the_role_over(self):
        out = self.fm_json("rotate", "--project", self.slug, self.orch["sid"], "--transport", "fake", "--json")
        new = out["new"]
        self.assertEqual(new["role"], "orchestrator")
        self.assertEqual(self.session(self.orch["sid"])["lifecycle"], "rotated")
        from foreman import projects
        self.assertEqual(projects.load_project(self.slug)["orchestrator"], new["sid"])


if __name__ == "__main__":
    unittest.main()
