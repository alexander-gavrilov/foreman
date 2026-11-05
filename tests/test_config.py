import unittest

from tests.helpers import ForemanTestCase


class ConfigTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import config
        self.c = config

    def test_defaults(self):
        cfg = self.c.load(platform="linux")
        self.assertEqual(cfg["web"]["port"], 8765)
        self.assertEqual(cfg["web"]["bind"], ["127.0.0.1"])
        self.assertEqual(cfg["thresholds"]["context_soft"], 150000)
        self.assertEqual(cfg["thresholds"]["context_hard"], 250000)
        self.assertEqual(cfg["thresholds"]["stuck_after_s"], 1200)
        self.assertEqual(cfg["thresholds"]["waiting_after_s"], 900)
        self.assertEqual(cfg["thresholds"]["limit_warn"], 70)
        self.assertEqual(cfg["thresholds"]["limit_stop"], 90)
        self.assertEqual(cfg["thresholds"]["work_ttl_days"], 14)
        self.assertEqual(cfg["upstream"]["repo"], "alexander-gavrilov/foreman")
        self.assertEqual(cfg["telegram"]["api_base"], "https://api.telegram.org")
        self.assertIsNone(cfg["telegram"]["chat_id"])
        for k in ("routine", "design", "review", "research", "orchestrator"):
            self.assertIn(k, cfg["routing"])

    def test_linux_is_home(self):
        cfg = self.c.load(platform="linux")
        self.assertEqual(cfg["machine"]["kind"], "home")
        self.assertEqual(cfg["upstream"]["mode"], "auto")
        self.assertEqual(cfg["notify"]["channels"], ["telegram"])

    def test_darwin_is_work(self):
        cfg = self.c.load(platform="darwin")
        self.assertEqual(cfg["machine"]["kind"], "work")
        self.assertEqual(cfg["upstream"]["mode"], "draft")
        self.assertEqual(cfg["notify"]["channels"], ["macos"])

    def test_override_kind_drives_derived_defaults(self):
        self.write_config({"machine": {"kind": "work"}})
        cfg = self.c.load(platform="linux")
        self.assertEqual(cfg["machine"]["kind"], "work")
        self.assertEqual(cfg["upstream"]["mode"], "draft")
        self.assertEqual(cfg["notify"]["channels"], ["macos"])

    def test_explicit_derived_values_win(self):
        self.write_config({"upstream": {"mode": "off"}, "notify": {"channels": ["file"]}})
        cfg = self.c.load(platform="darwin")
        self.assertEqual(cfg["upstream"]["mode"], "off")
        self.assertEqual(cfg["notify"]["channels"], ["file"])

    def test_deep_merge_keeps_sibling_defaults(self):
        self.write_config({"thresholds": {"context_soft": 100000}, "web": {"port": 9000}})
        cfg = self.c.load(platform="linux")
        self.assertEqual(cfg["thresholds"]["context_soft"], 100000)
        self.assertEqual(cfg["thresholds"]["context_hard"], 250000)
        self.assertEqual(cfg["web"]["port"], 9000)
        self.assertEqual(cfg["web"]["bind"], ["127.0.0.1"])

    def test_get_dotted(self):
        self.write_config({"thresholds": {"context_soft": 1}})
        self.assertEqual(self.c.get("thresholds.context_soft", platform="linux"), 1)
        self.assertEqual(self.c.get("web.bind", platform="linux"), ["127.0.0.1"])
        self.assertIsNone(self.c.get("nope.nothing"))
        self.assertEqual(self.c.get("nope.nothing", 5), 5)

    def test_corrupt_config_falls_back_to_defaults(self):
        import os
        with open(os.path.join(self.foreman_home, "config.json"), "w") as f:
            f.write("{oops")
        self.assertEqual(self.c.load(platform="linux")["web"]["port"], 8765)

    def test_defaults_not_mutated_between_calls(self):
        cfg = self.c.load(platform="linux")
        cfg["web"]["bind"].append("x")
        self.assertEqual(self.c.load(platform="linux")["web"]["bind"], ["127.0.0.1"])


class PathsTest(ForemanTestCase):
    def test_foreman_home_env(self):
        from foreman import paths
        self.assertEqual(paths.home(), self.foreman_home)

    def test_default_home_uses_xdg_then_home(self):
        import os
        from foreman import paths
        del os.environ["FOREMAN_HOME"]
        self.assertEqual(paths.home(), os.path.join(self.home, ".local", "state", "foreman"))
        os.environ["XDG_STATE_HOME"] = os.path.join(self.tmp, "xdg")
        self.assertEqual(paths.home(), os.path.join(self.tmp, "xdg", "foreman"))

    def test_layout(self):
        import os
        from foreman import paths
        h = self.foreman_home
        self.assertEqual(paths.config_file(), h + "/config.json")
        self.assertEqual(paths.secrets_file(), h + "/secrets.json")
        self.assertEqual(paths.project_dir("p"), h + "/projects/p")
        self.assertEqual(paths.project_file("p"), h + "/projects/p/project.json")
        self.assertEqual(paths.board_file("p"), h + "/projects/p/board.json")
        self.assertEqual(paths.journal_file("p"), h + "/projects/p/journal.jsonl")
        self.assertEqual(paths.chat_file("p"), h + "/projects/p/chat.jsonl")
        self.assertEqual(paths.lessons_file("p"), h + "/projects/p/lessons.md")
        self.assertEqual(paths.session_file("s-1"), h + "/sessions/s-1.json")
        self.assertEqual(paths.inbox_file("s-1"), h + "/inbox/s-1.jsonl")
        self.assertEqual(paths.limits_file("claude"), h + "/limits/claude.json")
        self.assertEqual(paths.events_file(), h + "/events.jsonl")
        self.assertEqual(paths.watch_state_file(), h + "/watch/state.json")
        self.assertEqual(paths.watch_heartbeat_file(), h + "/watch/heartbeat.json")
        self.assertEqual(paths.finding_file("f1"), h + "/findings/f1.json")
        self.assertEqual(paths.agent_work_dir("p"), h + "/agent-work/p")
        self.assertEqual(paths.reply_file("m-1"), h + "/replies/m-1.json")
        self.assertEqual(paths.projects_dir(), h + "/projects")
        self.assertEqual(paths.sessions_dir(), h + "/sessions")
        self.assertEqual(paths.findings_dir(), h + "/findings")
        self.assertEqual(paths.replies_dir(), h + "/replies")

    def test_rejects_path_traversal(self):
        from foreman import paths
        for bad in ("../x", "a/b", "", ".."):
            with self.assertRaises(ValueError):
                paths.project_dir(bad)
            with self.assertRaises(ValueError):
                paths.session_file(bad)


if __name__ == "__main__":
    unittest.main()
