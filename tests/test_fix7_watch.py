"""Fix round 7 (review 6 F10): a one-off `watch run` leaves the loop's heartbeat alone; the web UI uses the same
"running" rule as `watch list`."""
import contextlib
import io
import os

from tests.helpers import ForemanTestCase


def run_main(*args):
    from foreman import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args))
    return code, out.getvalue(), err.getvalue()


class HeartbeatTest(ForemanTestCase):
    def beat(self, **kw):
        from foreman import paths, store, timeutil
        hb = dict({"pid": os.getpid(), "last_run_at": timeutil.now_iso(), "interval_s": 60}, **kw)
        store.write_json(paths.watch_heartbeat_file(), hb)
        return hb

    def read_beat(self):
        from foreman import paths, store
        return store.read_json(paths.watch_heartbeat_file(), None)

    def test_one_off_run_does_not_overwrite_the_loops_heartbeat(self):
        hb = self.beat(pid=os.getpid() + 1, interval_s=17)
        code, out, err = run_main("watch", "run")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read_beat(), hb)

    def test_one_off_run_without_a_loop_shows_not_running(self):
        code, out, err = run_main("watch", "run")
        self.assertEqual(code, 0, err)
        code, out, err = run_main("watch", "list")
        self.assertIn("NOT RUNNING", out)

    def test_loop_passes_still_write_the_heartbeat(self):
        from foreman import watch
        watch.run_pass(interval_s=7)
        self.assertEqual(self.read_beat()["interval_s"], 7)


class WebRunningTest(ForemanTestCase):
    def status(self):
        from foreman.web import state_view
        proj = self.init_project("demo")
        return state_view.build_project(proj["slug"])["watches"][0]["status"]

    def test_no_heartbeat(self):
        self.assertEqual(self.status(), "not running")

    def test_dead_loop_with_a_fresh_heartbeat_is_not_running(self):
        from foreman import paths, store, timeutil
        store.write_json(paths.watch_heartbeat_file(), {"pid": 2 ** 22 + 12345, "last_run_at": timeutil.now_iso(),
                                                        "interval_s": 60})
        self.assertEqual(self.status(), "not running")

    def test_live_loop_is_running(self):
        from foreman import paths, store, timeutil
        store.write_json(paths.watch_heartbeat_file(), {"pid": os.getpid(), "last_run_at": timeutil.now_iso(),
                                                        "interval_s": 60})
        self.assertTrue(self.status().startswith("running, last pass"))

    def test_stale_heartbeat_of_a_live_pid_is_not_running(self):
        from foreman import paths, store
        store.write_json(paths.watch_heartbeat_file(), {"pid": os.getpid(), "last_run_at": "2020-01-01T00:00:00Z",
                                                        "interval_s": 60})
        self.assertEqual(self.status(), "not running")
