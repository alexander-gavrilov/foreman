"""Lead-found defect: the Telegram mirror thread resolved FOREMAN_HOME late and, after a test's teardown, logged into
the real ~/.local/state/foreman. The home is captured at spawn; the test base class flushes mirrors and asserts the
default home was not touched."""
import os
import tempfile
import threading
import unittest
from unittest import mock

from tests import helpers
from tests.helpers import ForemanTestCase


class MirrorHomeIsCapturedAtSpawnTest(ForemanTestCase):
    def test_a_late_mirror_error_is_logged_into_the_home_it_was_spawned_in(self):
        from foreman import messaging, notify, paths, store
        release = threading.Event()

        def slow_failure(*a, **k):
            release.wait(10)
            raise RuntimeError("telegram unreachable")
        spawned_home = paths.home()
        other = tempfile.mkdtemp(prefix="foreman-test-other-")
        self.addCleanup(__import__("shutil").rmtree, other, ignore_errors=True)
        with mock.patch.object(notify, "mirror_to_topic", side_effect=slow_failure):
            messaging._mirror("demo", "text", "m-1")
            os.environ["FOREMAN_HOME"] = other  # the process environment moves on (a test teardown, a cd)
            release.set()
            messaging.flush_mirrors(10)
        self.assertFalse(os.path.exists(os.path.join(other, "events.jsonl")))
        rows = store.read_jsonl(os.path.join(spawned_home, "events.jsonl"))
        self.assertEqual([r["kind"] for r in rows], ["mirror_error"])
        self.assertIn("telegram unreachable", rows[0]["error"])


class BaseClassGuardTest(unittest.TestCase):
    def _run(self, case_cls):
        result = unittest.TestResult()
        unittest.defaultTestLoader.loadTestsFromTestCase(case_cls).run(result)
        return result

    def test_a_test_that_creates_the_default_home_fails(self):
        real = tempfile.mkdtemp(prefix="foreman-test-realhome-")
        self.addCleanup(__import__("shutil").rmtree, real, ignore_errors=True)
        target = os.path.join(real, "foreman")

        class Touches(ForemanTestCase):
            def test_it(self):
                os.makedirs(target)

        with mock.patch.object(helpers, "real_default_homes", return_value=[target]):
            result = self._run(Touches)
        self.assertEqual(len(result.failures) + len(result.errors), 1, result.failures + result.errors)
        self.assertIn("default foreman home", (result.failures + result.errors)[0][1])

    def test_a_mirror_error_row_leaked_into_an_existing_default_home_fails(self):
        real = tempfile.mkdtemp(prefix="foreman-test-realhome-")
        self.addCleanup(__import__("shutil").rmtree, real, ignore_errors=True)

        class Leaks(ForemanTestCase):
            def test_it(self):
                with open(os.path.join(real, "events.jsonl"), "a") as f:
                    f.write('{"kind": "mirror_error"}\n')

        with mock.patch.object(helpers, "real_default_homes", return_value=[real]):
            result = self._run(Leaks)
        self.assertEqual(len(result.failures) + len(result.errors), 1)

    def test_a_test_that_leaves_it_alone_passes(self):
        real = tempfile.mkdtemp(prefix="foreman-test-realhome-")
        self.addCleanup(__import__("shutil").rmtree, real, ignore_errors=True)

        class Quiet(ForemanTestCase):
            def test_it(self):
                pass

        with mock.patch.object(helpers, "real_default_homes", return_value=[os.path.join(real, "foreman")]):
            self.assertTrue(self._run(Quiet).wasSuccessful())

    def test_the_base_class_flushes_mirrors_before_the_environment_is_restored(self):
        from foreman import messaging, notify
        started, release, order = threading.Event(), threading.Event(), []

        def slow(*a, **k):
            started.set()
            release.wait(5)
            order.append(("done", os.environ.get("FOREMAN_HOME")))
            return {"ok": True}

        class Spawns(ForemanTestCase):
            def test_it(self):
                messaging._mirror("demo", "text", "m-1")
                started.wait(5)
                threading.Timer(0.2, release.set).start()

        with mock.patch.object(notify, "mirror_to_topic", side_effect=slow):
            result = self._run(Spawns)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual(len(order), 1)
        self.assertIn("foreman-test-", order[0][1])  # it finished while the temp home was still in place
