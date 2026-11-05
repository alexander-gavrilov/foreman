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

    def _write_corrupt(self, text="{not json"):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            f.write(text)

    def _aside(self):
        return [n for n in os.listdir(os.path.dirname(self.path)) if ".corrupt-" in n]

    def test_corrupt_file_is_moved_aside_and_raises(self):
        self._write_corrupt()
        with self.assertRaises(self.store.StateCorrupt) as cm:
            self.store.read_json(self.path, [])
        self.assertFalse(os.path.exists(self.path))
        aside = self._aside()
        self.assertEqual(len(aside), 1)
        self.assertTrue(aside[0].startswith("b.json.corrupt-"))
        self.assertIn(aside[0], str(cm.exception))
        with open(os.path.join(os.path.dirname(self.path), aside[0])) as f:
            self.assertEqual(f.read(), "{not json")  # evidence is preserved
        # after quarantine the file is simply missing
        self.assertEqual(self.store.read_json(self.path, "dflt"), "dflt")

    def test_two_quarantines_do_not_overwrite_each_other(self):
        for _ in range(2):
            self._write_corrupt()
            with self.assertRaises(self.store.StateCorrupt):
                self.store.read_json(self.path)
        self.assertEqual(len(self._aside()), 2)

    def test_update_json_on_corrupt_does_not_write(self):
        self._write_corrupt()
        with self.assertRaises(self.store.StateCorrupt):
            self.store.update_json(self.path, lambda d: {"fresh": True}, default={})
        self.assertFalse(os.path.exists(self.path))

    def test_empty_file_counts_as_corrupt(self):
        self._write_corrupt("")
        with self.assertRaises(self.store.StateCorrupt):
            self.store.read_json(self.path)

    def test_state_corrupt_is_a_foreman_error(self):
        from foreman import errors
        self.assertTrue(issubclass(self.store.StateCorrupt, errors.ForemanError))

    def test_write_json_exclusive(self):
        self.store.write_json_exclusive(self.path, {"a": 1})
        self.assertEqual(self.store.read_json(self.path), {"a": 1})
        with self.assertRaises(FileExistsError):
            self.store.write_json_exclusive(self.path, {"a": 2})
        self.assertEqual(self.store.read_json(self.path), {"a": 1})
        leftovers = [n for n in os.listdir(os.path.dirname(self.path)) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_rewrite_jsonl_backs_up_when_it_had_corrupt_lines(self):
        p = os.path.join(self.foreman_home, "j.jsonl")
        self.store.append_jsonl(p, {"i": 1})
        with open(p, "a") as f:
            f.write("garbage\n")
        self.store.rewrite_jsonl(p, lambda rows: rows)
        backups = [n for n in os.listdir(self.foreman_home) if n.startswith("j.jsonl.corrupt-")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.store.read_jsonl(p), [{"i": 1}])

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


class StoreMultiProcessTest(ForemanTestCase):
    def test_update_json_across_processes(self):
        from foreman import store
        from tests import mp_workers
        path = os.path.join(self.foreman_home, "counter.json")
        store.write_json(path, {"n": 0})
        mp_workers.run_in_processes(mp_workers.increment, [(path, 25)] * 4)
        self.assertEqual(store.read_json(path)["n"], 100)

    def test_append_jsonl_across_processes_never_interleaves(self):
        from foreman import store
        from tests import mp_workers
        path = os.path.join(self.foreman_home, "log.jsonl")
        mp_workers.run_in_processes(mp_workers.append_rows, [(path, t, 30) for t in range(4)])
        with open(path) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 120)
        rows = [json.loads(l) for l in lines]  # every line is intact JSON
        for t in range(4):
            self.assertEqual([r["i"] for r in rows if r["tag"] == t], list(range(30)))


if __name__ == "__main__":
    unittest.main()
