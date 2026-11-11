"""How a settings file is edited (spec 5.5, "How a file is edited"): narrow, locked, atomic, idempotent, backed up."""
import json
import os
import stat
import threading

from tests.helpers import ForemanTestCase
from foreman import trustfile as tf  # noqa: E402
from foreman.trustfile import TrustEditError  # noqa: E402


def rd(path, mode="r"):
    with open(path, mode) as f:
        return f.read()


def mk(path, text, mode=0o640):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, mode)


def add(value, key="trustedWorkspaces"):
    """The pieces `edit` needs, for adding `value` to a top-level JSON list."""
    def build(text, existed):
        if not existed:
            return '{"%s": [%s]}\n' % (key, json.dumps(value))
        data = json.loads(text)
        if value in (data.get(key) or []):
            return None
        return tf.json_add_to_list(text, key, value)

    def verify(old, new):
        want = json.loads(old) if old.strip() else {}
        want[key] = list(want.get(key) or []) + [value]
        if json.loads(new) != want:
            raise TrustEditError("more than the entry would change")

    def present(text):
        return value in (json.loads(text).get(key) or [])
    return build, verify, present


class JsonTextTest(ForemanTestCase):
    SHAPES = {
        "multiline": '{\n  "a": 1,\n  "trustedWorkspaces": [\n    "/x",\n    "/y"\n  ]\n}\n',
        "inline": '{"trustedWorkspaces":["/x"]}',
        "no key": '{\n  "a": 1\n}\n',
        "empty object": "{}",
        "empty list": '{"trustedWorkspaces": []}',
        "key in the middle": '{\n  "trustedWorkspaces": [\n    "/x"\n  ],\n  "b": {"c": ["]", "}"]}\n}',
        "tabs": '{\n\t"a": 1,\n\t"trustedWorkspaces": [\n\t\t"/x"\n\t]\n}\n',
        "strings with brackets and quotes": '{"s": "a\\"]}", "trustedWorkspaces": ["/x \\"y\\""]}',
    }

    def test_add_then_remove_gives_the_original_bytes(self):
        for name, text in self.SHAPES.items():
            with self.subTest(name):
                new = tf.json_add_to_list(text, "trustedWorkspaces", "/new")
                self.assertIn("/new", json.loads(new)["trustedWorkspaces"])
                created = "trustedWorkspaces" not in json.loads(text)
                back = tf.json_remove_from_list(new, "trustedWorkspaces", "/new", drop_key_if_empty=created)
                self.assertEqual(back, text)

    def test_the_rest_of_the_text_survives_byte_for_byte(self):
        text = '{\n  "a"  :  1,  "trustedWorkspaces" : [ "/x" ],\n"z":\t{"q": [1, 2]}\n}'
        new = tf.json_add_to_list(text, "trustedWorkspaces", "/n")
        cut = text.index('"/x"') + 4
        self.assertTrue(new.startswith(text[:cut]))
        self.assertTrue(new.endswith(text[cut:]))
        self.assertEqual(json.loads(new)["trustedWorkspaces"], ["/x", "/n"])

    def test_remove_of_a_missing_entry_is_none(self):
        self.assertIsNone(tf.json_remove_from_list('{"trustedWorkspaces": ["/x"]}', "trustedWorkspaces", "/y"))
        self.assertIsNone(tf.json_remove_from_list("{}", "trustedWorkspaces", "/y"))

    def test_remove_first_and_middle_elements(self):
        text = '{"w": ["/a", "/b", "/c"]}'
        self.assertEqual(tf.json_remove_from_list(text, "w", "/a"), '{"w": ["/b", "/c"]}')
        self.assertEqual(tf.json_remove_from_list(text, "w", "/b"), '{"w": ["/a", "/c"]}')
        self.assertEqual(tf.json_remove_from_list(text, "w", "/c"), '{"w": ["/a", "/b"]}')

    def test_paths_are_encoded_by_a_real_encoder(self):
        for value in ('/a "q"/b', "/back\\slash", "/üñí/😀", "/tab\there"):
            with self.subTest(value):
                new = tf.json_add_to_list('{"w": []}', "w", value)
                self.assertEqual(json.loads(new)["w"], [value])

    def test_strict_parser_refuses_bom_comments_and_nan(self):
        for bad in ('﻿{"a": 1}', '{"a": 1} // c', '{"a": NaN}', "{'a': 1}", '{"a": 1,}', ""):
            with self.subTest(bad):
                with self.assertRaises(TrustEditError):
                    tf.parse_json_strict(bad)

    def test_list_shape_is_checked(self):
        self.assertEqual(tf.json_list({"w": ["a"]}, "w"), ["a"])
        self.assertIsNone(tf.json_list({}, "w"))
        for bad in ({"w": "a"}, {"w": [1]}, {"w": {"a": 1}}, {"w": None}):
            with self.assertRaises(TrustEditError):
                tf.json_list(bad, "w")
        with self.assertRaises(TrustEditError):
            tf.json_list([], "w")


class TomlTextTest(ForemanTestCase):
    def test_keys_and_values_are_encoded_by_a_real_encoder(self):
        import tomllib
        for key in ('/a/b', '/a "q"/b', '/back\\slash', '/üñí', '/x:y:0:0.json'):
            with self.subTest(key):
                text = tf.toml_table_text(["projects", key], {"trust_level": "trusted"})
                self.assertEqual(tomllib.loads(text), {"projects": {key: {"trust_level": "trusted"}}})

    def test_a_control_character_is_refused(self):
        for bad in ("/a\nb", "/a\x00b", "/a\x7fb", "/a\x1bb"):
            with self.assertRaises(TrustEditError):
                tf.toml_string(bad)

    def test_append_and_exact_removal(self):
        for old in ("", "a = 1\n", "a = 1", "a = 1\n\n[t]\nx = 2\n"):
            with self.subTest(old):
                blocks = [tf.toml_table_text(["projects", "/p"], {"trust_level": "trusted"}),
                          tf.toml_table_text(["hooks", "state", "k:1"], {"enabled": True, "trusted_hash": "sha256:ab"})]
                new, added = tf.toml_append_tables(old, blocks)
                self.assertTrue(new.startswith(old))
                import tomllib
                tomllib.loads(new)
                back = new
                for text in reversed(added):
                    back = tf.toml_remove_text(back, text)
                self.assertEqual(back, old)

    def test_removal_of_text_that_is_not_there(self):
        self.assertIsNone(tf.toml_remove_text("a = 1\n", "[t]\n"))


class EditTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        self.dir = os.path.join(self.tmp, "cfg")
        self.path = os.path.join(self.dir, "settings.json")

    def run_edit(self, value="/new", path=None, **kw):
        build, verify, present = add(value)
        return tf.edit(path or self.path, build, verify, present, **kw)

    def test_edits_in_place_keeping_permissions_and_making_one_backup(self):
        mk(self.path, '{\n  "a": 1\n}\n', 0o640)
        original = rd(self.path, "rb")
        self.assertEqual(self.run_edit(), "edited")
        self.assertEqual(json.loads(rd(self.path))["trustedWorkspaces"], ["/new"])
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o640)
        backup = self.path + ".foreman-bak"
        self.assertEqual(rd(backup, "rb"), original)
        self.assertEqual(stat.S_IMODE(os.stat(backup).st_mode), 0o640)
        self.assertEqual(self.run_edit("/second"), "edited")
        self.assertEqual(rd(backup, "rb"), original)  # never overwritten
        self.assertEqual([n for n in os.listdir(self.dir)], ["settings.json", "settings.json.foreman-bak"])

    def test_the_backup_is_not_readable_by_others_when_the_original_is_not(self):
        mk(self.path, "{}", 0o600)
        self.run_edit()
        self.assertEqual(stat.S_IMODE(os.stat(self.path + ".foreman-bak").st_mode), 0o600)

    def test_nothing_to_do_writes_nothing_and_makes_no_backup(self):
        mk(self.path, '{"trustedWorkspaces": ["/new"]}')
        before = os.stat(self.path)
        self.assertEqual(self.run_edit(), "unchanged")
        after = os.stat(self.path)
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertFalse(os.path.exists(self.path + ".foreman-bak"))

    def test_a_missing_file_is_created_0600(self):
        os.makedirs(self.dir)
        self.assertEqual(self.run_edit(), "created")
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual(json.loads(rd(self.path)), {"trustedWorkspaces": ["/new"]})
        self.assertFalse(os.path.exists(self.path + ".foreman-bak"))

    def test_a_missing_folder_is_refused(self):
        with self.assertRaises(TrustEditError):
            self.run_edit()
        self.assertFalse(os.path.exists(self.dir))

    def test_a_file_that_is_not_valid_is_left_exactly_as_it_was(self):
        for text in ("{not json", '﻿{"a": 1}', '{"a": 1} // c', "[]", '{"trustedWorkspaces": "x"}',
                     '{"trustedWorkspaces": [1]}', ""):
            with self.subTest(text):
                mk(self.path, text)

                def build(old, existed):
                    tf.json_list(tf.parse_json_strict(old), "trustedWorkspaces")
                    return tf.json_add_to_list(old, "trustedWorkspaces", "/n")
                with self.assertRaises(TrustEditError):
                    tf.edit(self.path, build, lambda o, n: None, lambda t: True)
                self.assertEqual(rd(self.path), text)
                self.assertFalse(os.path.exists(self.path + ".foreman-bak"))
                self.assertEqual(os.listdir(self.dir), ["settings.json"])

    def test_an_edit_that_changes_more_than_intended_is_not_written(self):
        mk(self.path, '{"a": 1}')
        build, _, present = add("/n")

        def bad_build(text, existed):
            return '{"a": 2, "trustedWorkspaces": ["/n"]}'
        _, verify, _ = add("/n")
        with self.assertRaises(TrustEditError):
            tf.edit(self.path, bad_build, verify, present)
        self.assertEqual(rd(self.path), '{"a": 1}')
        self.assertFalse(os.path.exists(self.path + ".foreman-bak"))

    def test_a_link_is_followed_and_stays_a_link(self):
        real = os.path.join(self.tmp, "dotfiles", "settings.json")
        mk(real, "{}")
        os.makedirs(self.dir)
        os.symlink(real, self.path)
        self.run_edit()
        self.assertTrue(os.path.islink(self.path))
        self.assertEqual(json.loads(rd(real))["trustedWorkspaces"], ["/new"])
        self.assertTrue(os.path.exists(real + ".foreman-bak"))
        self.assertFalse(os.path.exists(self.path + ".foreman-bak"))

    def test_a_file_that_is_not_a_regular_file_is_refused(self):
        os.makedirs(self.path)  # a directory
        with self.assertRaises(TrustEditError):
            self.run_edit()
        fifo = os.path.join(self.dir, "fifo.json")
        os.mkfifo(fifo)
        with self.assertRaises(TrustEditError):
            self.run_edit(path=fifo)

    def test_an_unwritable_folder_is_refused(self):
        mk(self.path, "{}")
        os.chmod(self.dir, 0o500)
        self.addCleanup(os.chmod, self.dir, 0o700)
        if os.access(self.dir, os.W_OK):
            self.skipTest("running as a user that ignores modes")
        with self.assertRaises(TrustEditError):
            self.run_edit()
        self.assertEqual(rd(self.path), "{}")

    def test_a_change_between_read_and_rename_starts_over(self):
        mk(self.path, '{"a": 1}')
        build, verify, present = add("/n")
        calls = []

        def racing_build(text, existed):
            calls.append(text)
            if len(calls) == 1:  # the agent rewrites its file while foreman holds only its own lock
                mk(self.path, '{"a": 1, "b": 2}')
                return build(text, existed)
            return build(text, existed)
        # the first build sees the old text and the file changes right after; the compare notices
        orig_read = tf.read_bytes
        state = {"n": 0}

        def read_bytes(path):
            state["n"] += 1
            if state["n"] == 2 and path == os.path.realpath(self.path):  # the compare just before the rename
                mk(self.path, '{"a": 1, "b": 2}')
            return orig_read(path)
        tf.read_bytes = read_bytes
        self.addCleanup(setattr, tf, "read_bytes", orig_read)
        self.assertEqual(tf.edit(self.path, build, verify, present), "edited")
        data = json.loads(rd(self.path))
        self.assertEqual(data, {"a": 1, "b": 2, "trustedWorkspaces": ["/n"]})  # the agent's change is kept

    def test_three_lost_races_give_up_and_report(self):
        mk(self.path, '{"a": 1}')
        build, verify, _ = add("/n")
        dropped = []
        n = {"i": 0}

        def agent_rewrites_after_rename(text):  # read-back: the agent dropped the entry again
            n["i"] += 1
            mk(self.path, '{"a": 1}')
            return False
        with self.assertRaises(TrustEditError) as cm:
            tf.edit(self.path, build, verify, agent_rewrites_after_rename, on_abort=lambda: dropped.append(1))
        self.assertEqual(n["i"], 3)
        self.assertIn("kept changing", str(cm.exception))
        self.assertEqual(dropped, [1])

    def test_the_record_hooks_run_in_order_and_pending_comes_first(self):
        mk(self.path, "{}")
        log = []
        self.run_edit(before_write=lambda: log.append(("pending", "/new" in rd(self.path))),
                      after_applied=lambda: log.append(("applied", "/new" in rd(self.path))))
        self.assertEqual(log, [("pending", False), ("applied", True)])

    def test_the_lock_file_lives_in_the_state_folder_not_beside_the_target(self):
        mk(self.path, "{}")
        self.run_edit()
        self.assertEqual(sorted(os.listdir(self.dir)), ["settings.json", "settings.json.foreman-bak"])
        locks = os.listdir(os.path.join(self.foreman_home, "locks"))
        self.assertTrue(locks and all(n.startswith("edit-") for n in locks))

    def test_two_edits_at_once_lose_neither_entry(self):
        mk(self.path, "{}")
        errors = []

        def one(value):
            try:
                self.run_edit(value)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)
        threads = [threading.Thread(target=one, args=("/w%d" % i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(json.loads(rd(self.path))["trustedWorkspaces"]),
                         sorted("/w%d" % i for i in range(8)))

    def test_no_temporary_file_is_left_after_a_failure(self):
        mk(self.path, "{}")
        _, verify, present = add("/n")
        with self.assertRaises(RuntimeError):
            tf.edit(self.path, lambda t, e: (_ for _ in ()).throw(RuntimeError("boom")), verify, present)
        self.assertEqual(os.listdir(self.dir), ["settings.json"])
