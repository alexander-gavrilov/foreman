import os
import unittest

from tests.helpers import ForemanTestCase


class ProjectsTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import projects
        self.p = projects

    def test_slugify(self):
        self.assertEqual(self.p.slugify("My Cool_Project!"), "my-cool-project")
        self.assertEqual(self.p.slugify("  --x--  "), "x")
        self.assertEqual(self.p.slugify("!!!"), "project")

    def test_create_and_load(self):
        folder = self.make_dir("Shopping List")
        proj = self.p.create_project(folder)
        self.assertEqual(proj["slug"], "shopping-list")
        self.assertEqual(proj["name"], "Shopping List")
        self.assertEqual(proj["path"], folder)
        self.assertEqual(proj["gates"], [])
        self.assertIsNone(proj["orchestrator"])
        self.assertIsNone(proj["telegram_topic_id"])
        self.assertTrue(proj["created_at"].endswith("Z"))
        self.assertEqual(self.p.load_project("shopping-list"), proj)
        self.assertTrue(os.path.exists(os.path.join(self.foreman_home, "projects/shopping-list/board.json")))

    def test_create_is_idempotent_for_same_path(self):
        folder = self.make_dir("demo")
        a = self.p.create_project(folder)
        b = self.p.create_project(folder)
        self.assertEqual(a, b)
        self.assertEqual(len(self.p.list_projects()), 1)

    def test_slug_collision_for_different_paths(self):
        a = self.p.create_project(self.make_dir("one/app"))
        b = self.p.create_project(self.make_dir("two/app"))
        self.assertEqual(a["slug"], "app")
        self.assertEqual(b["slug"], "app-2")

    def test_load_missing(self):
        self.assertIsNone(self.p.load_project("nope"))

    def test_list_sorted(self):
        self.p.create_project(self.make_dir("zeta"))
        self.p.create_project(self.make_dir("alpha"))
        self.assertEqual([x["slug"] for x in self.p.list_projects()], ["alpha", "zeta"])

    def test_project_for_path(self):
        root = self.make_dir("demo")
        sub = self.make_dir("demo/src/deep")
        other = self.make_dir("other")
        self.p.create_project(root)
        self.assertEqual(self.p.project_for_path(root)["slug"], "demo")
        self.assertEqual(self.p.project_for_path(sub)["slug"], "demo")
        self.assertIsNone(self.p.project_for_path(other))
        self.assertIsNone(self.p.project_for_path(os.path.join(self.work, "demo-not")))

    def test_project_for_path_prefers_deepest(self):
        self.p.create_project(self.make_dir("mono"))
        self.p.create_project(self.make_dir("mono/pkg"))
        self.assertEqual(self.p.project_for_path(self.make_dir("mono/pkg/x"))["slug"], "pkg")

    def test_project_for_path_follows_symlinks(self):
        root = self.make_dir("real")
        link = os.path.join(self.work, "link")
        os.symlink(root, link)
        self.p.create_project(root)
        self.assertEqual(self.p.project_for_path(link)["slug"], "real")

    def test_save_project(self):
        proj = self.init_project("demo")
        proj["gates"] = ["deploy"]
        self.p.save_project(proj)
        self.assertEqual(self.p.load_project("demo")["gates"], ["deploy"])


class BoardTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import projects
        self.p = projects
        self.init_project("demo")

    def test_empty_board(self):
        self.assertEqual(self.p.load_board("demo"), {"tasks": [], "backlog": []})

    def test_add_task(self):
        t = self.p.add_task("demo", "Write parser", stage="build", owner="s-1", next="tests",
                            links=[{"label": "pr", "url": "http://x/1"}])
        self.assertEqual(t["id"], "t1")
        self.assertEqual(t["title"], "Write parser")
        self.assertEqual(t["stage"], "build")
        self.assertEqual(t["status"], "open")
        self.assertEqual(t["links"], [{"label": "pr", "url": "http://x/1"}])
        self.assertIsNone(t["check"])
        self.assertTrue(t["updated_at"].endswith("Z"))
        t2 = self.p.add_task("demo", "Second")
        self.assertEqual(t2["id"], "t2")
        self.assertEqual([x["id"] for x in self.p.list_tasks("demo")], ["t1", "t2"])

    def test_ids_never_reused(self):
        self.p.add_task("demo", "a")
        self.p.add_task("demo", "b")
        board = self.p.load_board("demo")
        board["tasks"].pop(0)
        self.p.save_board("demo", board)
        self.assertEqual(self.p.add_task("demo", "c")["id"], "t3")

    def test_set_fields(self):
        self.p.add_task("demo", "a")
        t = self.p.set_task_fields("demo", "t1", {"stage": "review", "owner": "s-2", "next": "merge"})
        self.assertEqual((t["stage"], t["owner"], t["next"]), ("review", "s-2", "merge"))
        self.assertEqual(self.p.get_task("demo", "t1")["stage"], "review")

    def test_set_unknown_task_and_field(self):
        self.p.add_task("demo", "a")
        with self.assertRaises(KeyError):
            self.p.set_task_fields("demo", "t9", {"stage": "x"})
        with self.assertRaises(ValueError):
            self.p.set_task_fields("demo", "t1", {"bogus": "x"})

    def test_set_status_done_is_rejected(self):
        self.p.add_task("demo", "a")
        with self.assertRaises(ValueError):
            self.p.set_task_fields("demo", "t1", {"status": "done"})

    def test_mark_done_records_check(self):
        self.p.add_task("demo", "a")
        t = self.p.mark_done("demo", "t1", check={"cmd": "true", "exit_code": 0, "tail": "ok"})
        self.assertEqual(t["status"], "done")
        self.assertEqual(t["check"]["exit_code"], 0)
        self.assertTrue(t["check"]["ran_at"].endswith("Z"))

    def test_record_failed_check_keeps_open(self):
        self.p.add_task("demo", "a")
        t = self.p.record_check("demo", "t1", {"cmd": "false", "exit_code": 1, "tail": "boom"})
        self.assertEqual(t["status"], "open")
        self.assertEqual(t["check"]["exit_code"], 1)


class JournalChatTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import projects
        self.p = projects
        self.init_project("demo")

    def test_journal_append_and_search(self):
        e = self.p.journal_append("demo", "orchestrator", "decision", "chose sqlite", {"why": "stdlib"})
        self.assertEqual(set(e), {"ts", "actor", "kind", "text", "data"})
        self.assertEqual(e["data"], {"why": "stdlib"})
        self.p.journal_append("demo", "user", "note", "deploy on Friday")
        hits = self.p.journal_search("demo", "SQLITE")
        self.assertEqual([h["text"] for h in hits], ["chose sqlite"])
        self.assertEqual(len(self.p.journal_search("demo", r"dep.oy")), 1)
        self.assertEqual(self.p.journal_search("demo", "nothing"), [])
        self.assertEqual(len(self.p.journal_tail("demo", 1)), 1)

    def test_chat_append_and_list(self):
        m = self.p.chat_append("demo", "orchestrator", "Deploy now?", question=True,
                               options=["Yes", "No"], channel="cli")
        self.assertRegex(m["id"], r"^m-[0-9a-f]{8}$")
        self.assertTrue(m["question"])
        self.assertEqual(m["options"], ["Yes", "No"])
        self.assertIsNone(m["answered_by"])
        self.assertEqual(m["from"], "orchestrator")
        self.p.chat_append("demo", "user", "Yes", reply_to=m["id"])
        msgs = self.p.chat_list("demo")
        self.assertEqual([x["from"] for x in msgs], ["orchestrator", "user"])
        self.assertEqual(msgs[1]["reply_to"], m["id"])
        self.assertEqual(len(self.p.chat_list("demo", limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
