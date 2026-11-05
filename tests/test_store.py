import json
import os
import threading
import unittest

from tests.helpers import ForemanTestCase


class StoreTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import store
        self.store = store
        self.path = os.path.join(self.foreman_home, "a", "b.json")

    def test_read_default_when_missing(self):
        self.assertEqual(self.store.read_json(self.path, {"x": 1}), {"x": 1})
        self.assertIsNone(self.store.read_json(self.path))

    def test_read_default_when_corrupt(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertEqual(self.store.read_json(self.path, []), [])

    def test_write_roundtrip_and_creates_dirs(self):
        self.store.write_json(self.path, {"k": "значение"})
        self.assertEqual(self.store.read_json(self.path), {"k": "значение"})
        self.assertTrue(os.path.exists(self.path + ".lock"))

    def test_write_leaves_no_tmp_files(self):
        self.store.write_json(self.path, {"a": 1})
        self.store.write_json(self.path, {"a": 2})
        names = sorted(os.listdir(os.path.dirname(self.path)))
        self.assertEqual(names, ["b.json", "b.json.lock"])

    def test_secret_mode(self):
        self.store.write_json(self.path, {"a": 1}, mode=0o600)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_update_json_read_modify_write(self):
        self.store.write_json(self.path, {"n": 0})
        self.store.update_json(self.path, lambda d: dict(d, n=d["n"] + 1), default={})
        self.assertEqual(self.store.read_json(self.path), {"n": 1})

    def test_update_json_concurrent_increments(self):
        self.store.write_json(self.path, {"n": 0})

        def work():
            for _ in range(20):
                self.store.update_json(self.path, lambda d: dict(d, n=d["n"] + 1), default={})
        threads = [threading.Thread(target=work) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(self.store.read_json(self.path)["n"], 100)

    def test_jsonl_append_read_tail(self):
        p = os.path.join(self.foreman_home, "j.jsonl")
        self.assertEqual(self.store.read_jsonl(p), [])
        for i in range(5):
            self.store.append_jsonl(p, {"i": i})
        self.assertEqual([r["i"] for r in self.store.read_jsonl(p)], [0, 1, 2, 3, 4])
        self.assertEqual([r["i"] for r in self.store.tail_jsonl(p, 2)], [3, 4])
        self.assertEqual(self.store.tail_jsonl(p, 0), [])

    def test_jsonl_skips_corrupt_lines(self):
        p = os.path.join(self.foreman_home, "j.jsonl")
        self.store.append_jsonl(p, {"i": 1})
        with open(p, "a") as f:
            f.write("garbage\n")
        self.store.append_jsonl(p, {"i": 2})
        self.assertEqual([r["i"] for r in self.store.read_jsonl(p)], [1, 2])

    def test_jsonl_concurrent_append(self):
        p = os.path.join(self.foreman_home, "j.jsonl")

        def work(k):
            for i in range(25):
                self.store.append_jsonl(p, {"k": k, "i": i})
        threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(self.store.read_jsonl(p)), 100)

    def test_search_jsonl(self):
        p = os.path.join(self.foreman_home, "j.jsonl")
        for t in ("Fix login bug", "deploy v2", "login redirect fixed", "other"):
            self.store.append_jsonl(p, {"text": t})
        hits = self.store.search_jsonl(p, "login", field="text")
        self.assertEqual([h["text"] for h in hits], ["Fix login bug", "login redirect fixed"])
        hits = self.store.search_jsonl(p, r"^deploy\b", field="text")
        self.assertEqual(len(hits), 1)
        hits = self.store.search_jsonl(p, "login", field="text", limit=1)
        self.assertEqual([h["text"] for h in hits], ["login redirect fixed"])  # newest wins

    def test_search_invalid_regex_falls_back_to_substring(self):
        p = os.path.join(self.foreman_home, "j.jsonl")
        self.store.append_jsonl(p, {"text": "call f(x"})
        self.assertEqual(len(self.store.search_jsonl(p, "f(x", field="text")), 1)

    def test_rewrite_jsonl(self):
        p = os.path.join(self.foreman_home, "j.jsonl")
        for i in range(3):
            self.store.append_jsonl(p, {"i": i})
        self.store.rewrite_jsonl(p, lambda rows: [r for r in rows if r["i"] != 1])
        self.assertEqual([r["i"] for r in self.store.read_jsonl(p)], [0, 2])


if __name__ == "__main__":
    unittest.main()
