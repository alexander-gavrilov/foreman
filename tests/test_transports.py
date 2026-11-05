import unittest

from tests.helpers import ForemanTestCase


class FakeTransportTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        from foreman import transports
        from foreman.transports import base
        self.base = base
        self.t = transports.get_transport("fake")

    def test_get_transport(self):
        from foreman import transports
        from foreman.transports.fake import FakeTransport
        self.assertIsInstance(self.t, FakeTransport)
        self.assertIsInstance(self.t, self.base.Transport)
        with self.assertRaises(ValueError):
            transports.get_transport("nonexistent")

    def test_requires_env(self):
        import os
        from foreman import transports
        del os.environ["FOREMAN_FAKE_TRANSPORT"]
        with self.assertRaises(RuntimeError):
            transports.get_transport("fake")

    def test_launch_list_alive(self):
        h = self.t.launch("claude", "sonnet", None, "/tmp/x", "do the thing", "fm-api")
        self.assertTrue(self.t.alive(h))
        terms = self.t.list()
        self.assertEqual(len(terms), 1)
        ti = terms[0]
        self.assertIsInstance(ti, self.base.TermInfo)
        self.assertEqual((ti.handle, ti.title, ti.cwd, ti.agent), (h, "fm-api", "/tmp/x", "claude"))
        self.assertTrue(ti.alive)

    def test_launch_prompt_is_first_input(self):
        h = self.t.launch("claude", "sonnet", None, "/tmp", "hello", "t")
        self.assertEqual(self.t.inputs(h), ["hello"])

    def test_send_records_input_and_dead_handle_raises(self):
        h = self.t.launch("codex", None, None, "/tmp", "", "t")
        self.t.send(h, "one")
        self.t.send(h, "two")
        self.assertEqual(self.t.inputs(h), ["one", "two"])  # empty prompt is not an input
        self.t.kill(h)
        self.assertFalse(self.t.alive(h))
        with self.assertRaises(self.base.TerminalGone):
            self.t.send(h, "three")
        with self.assertRaises(self.base.TerminalGone):
            self.t.send("no-such-handle", "x")

    def test_list_excludes_dead(self):
        h = self.t.launch("claude", None, None, "/tmp", "", "t")
        self.t.kill(h)
        self.assertEqual(self.t.list(), [])

    def test_close_marks_dead_and_is_idempotent(self):
        h = self.t.launch("claude", None, None, "/tmp", "", "t")
        self.t.close(h)
        self.assertFalse(self.t.alive(h))
        self.t.close(h)
        self.t.close("unknown")

    def test_rename(self):
        h = self.t.launch("claude", None, None, "/tmp", "", "old")
        self.t.rename(h, "new")
        self.assertEqual(self.t.list()[0].title, "new")
        self.t.kill(h)
        with self.assertRaises(self.base.TerminalGone):
            self.t.rename(h, "x")

    def test_read_returns_last_lines(self):
        h = self.t.launch("claude", None, None, "/tmp", "", "t")
        self.t.set_output(h, ["a", "b", "c"])
        self.assertEqual(self.t.read(h, 2), "b\nc")
        self.assertEqual(self.t.read(h, 10), "a\nb\nc")
        self.t.kill(h)
        with self.assertRaises(self.base.TerminalGone):
            self.t.read(h, 1)

    def test_wait_idle(self):
        h = self.t.launch("claude", None, None, "/tmp", "", "t")
        self.t.set_idle(h, False)
        self.assertFalse(self.t.wait_idle(h, timeout=0.2))
        self.t.set_idle(h, True)
        self.assertTrue(self.t.wait_idle(h, timeout=0.2))
        self.t.kill(h)
        with self.assertRaises(self.base.TerminalGone):
            self.t.wait_idle(h, timeout=0.2)

    def test_state_is_shared_between_instances(self):
        from foreman import transports
        h = self.t.launch("claude", None, None, "/tmp", "", "t")
        other = transports.get_transport("fake")
        other.send(h, "hi")
        self.assertEqual(self.t.inputs(h), ["hi"])


class RegistryTest(ForemanTestCase):
    def test_registry_is_explicit(self):
        from foreman import transports
        self.assertEqual(transports.REGISTRY, {
            "orca": ("foreman.transports.orca", "OrcaTransport"),
            "herdr": ("foreman.transports.herdr", "HerdrTransport"),
            "claude-msg": ("foreman.transports.claude_msg", "ClaudeMsgTransport"),
            "fake": ("foreman.transports.fake", "FakeTransport"),
        })

    def test_every_kind_resolves_to_its_class_via_the_registry(self):
        import sys, types
        from unittest import mock
        from foreman import transports
        for kind, (modname, clsname) in transports.REGISTRY.items():
            if kind == "fake":
                continue
            mod = types.ModuleType(modname)
            cls = type(clsname, (), {})
            setattr(mod, clsname, cls)
            with mock.patch.dict(sys.modules, {modname: mod}):
                self.assertIsInstance(transports.get_transport(kind), cls)

    def test_unknown_and_unsafe_kinds_are_rejected(self):
        from foreman import transports
        for bad in ("base", "nope", "../x", "claude_msg", "", None, "os"):
            with self.assertRaises(ValueError):
                transports.get_transport(bad)

    def test_registered_but_unimplemented_kind_says_so(self):
        import sys
        from unittest import mock
        from foreman import transports
        with mock.patch.dict(sys.modules, {"foreman.transports.herdr": None}):
            with self.assertRaises(ValueError) as cm:
                transports.get_transport("herdr")
        self.assertIn("herdr", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
