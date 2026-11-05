import json
import os

from tests.helpers import ForemanTestCase, run_cli, BIN
from foreman import install, paths  # noqa: E402
from foreman.commands import install as install_cmd, uninstall as uninstall_cmd  # noqa: E402
import argparse  # noqa: E402


class Runner:
    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def __call__(self, argv):
        self.calls.append(argv)
        return self.rc


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = (os.readlink(p) if os.path.islink(p) else open(p).read())
    return out


class InstallTest(ForemanTestCase):
    def setUp(self):
        super().setUp()
        os.environ.pop("FOREMAN_INSTALL_DRY_SERVICES", None)
        self.claude = os.path.join(self.home, ".claude", "settings.json")
        self.agy = os.path.join(self.home, ".gemini", "antigravity-cli", "settings.json")
        write(self.claude, json.dumps({"theme": "dark",
                                       "statusLine": {"type": "command", "command": "bash ~/sl.sh", "padding": 0}}))
        write(self.agy, json.dumps({"statusLine": {"type": "command", "command": "bash ~/agy.sh", "enabled": True}}))

    def sl(self, path):
        return json.load(open(path))["statusLine"]["command"]

    def test_linux_round_trip(self):
        before = snapshot(self.home)
        r = Runner()
        changes = install.install(machine_kind="home", bind=["127.0.0.1", "10.8.0.2"], port=9000,
                                  base_url="http://h.lan:9000", platform="linux", runner=r)
        link = os.path.join(self.home, ".local", "bin", "foreman")
        self.assertEqual(os.readlink(link), BIN)
        unit = open(os.path.join(self.home, ".config", "systemd", "user", "foreman.service")).read()
        self.assertIn("ExecStart=%s serve" % link, unit)
        self.assertIn("Restart=always", unit)
        self.assertEqual(r.calls[0], ["systemctl", "--user", "daemon-reload"])
        self.assertEqual(r.calls[1], ["systemctl", "--user", "enable", "--now", "foreman.service"])
        self.assertEqual(self.sl(self.claude), "%s tap statusline --agent claude -- sh -c 'bash ~/sl.sh'" % link)
        self.assertEqual(self.sl(self.agy), "%s tap statusline --agent antigravity -- sh -c 'bash ~/agy.sh'" % link)
        self.assertTrue(os.path.exists(self.claude + ".foreman-bak"))
        cfg = json.load(open(paths.config_file()))
        self.assertEqual(cfg["web"], {"bind": ["127.0.0.1", "10.8.0.2"], "port": 9000, "base_url": "http://h.lan:9000"})
        self.assertEqual(cfg["machine"]["kind"], "home")
        self.assertTrue(changes)
        # second run: nothing changes, no daemon-reload, no double wrap
        mid = snapshot(self.home)
        r2 = Runner()
        again = install.install(machine_kind="home", bind=["127.0.0.1", "10.8.0.2"], port=9000,
                                base_url="http://h.lan:9000", platform="linux", runner=r2)
        self.assertEqual(again, [])
        self.assertEqual(snapshot(self.home), mid)
        self.assertNotIn(["systemctl", "--user", "daemon-reload"], r2.calls)
        self.assertEqual(self.sl(self.claude).count("tap statusline"), 1)
        # uninstall restores everything under HOME
        r3 = Runner()
        install.uninstall(platform="linux", runner=r3)
        self.assertIn(["systemctl", "--user", "disable", "--now", "foreman.service"], r3.calls)
        self.assertEqual(snapshot(self.home), before)
        self.assertTrue(os.path.isdir(self.foreman_home))  # state kept

    def test_macos_round_trip(self):
        before = snapshot(self.home)
        r = Runner(rc=1)  # `launchctl print` fails -> not loaded -> bootstrap
        install.install(platform="darwin", runner=r)
        plist = os.path.join(self.home, "Library", "LaunchAgents", "com.foreman.serve.plist")
        text = open(plist).read()
        link = os.path.join(self.home, ".local", "bin", "foreman")
        self.assertIn("<string>%s</string>" % link, text)
        self.assertIn("<string>serve</string>", text)
        self.assertTrue(any(c[:2] == ["launchctl", "bootstrap"] and c[3] == plist for c in r.calls))
        self.assertEqual(r.calls[0][:2], ["launchctl", "print"])
        mid = snapshot(self.home)
        r2 = Runner(rc=0)  # loaded already, file unchanged -> no bootstrap
        self.assertEqual(install.install(platform="darwin", runner=r2, which=lambda b: "/usr/local/bin/" + b), [])
        self.assertEqual(snapshot(self.home), mid)
        self.assertFalse(any(c[1] == "bootstrap" for c in r2.calls))
        r3 = Runner()
        install.uninstall(platform="darwin", runner=r3)
        self.assertTrue(any(c[:2] == ["launchctl", "bootout"] for c in r3.calls))
        self.assertEqual(snapshot(self.home), before)

    def test_macos_terminal_notifier_installed_via_brew(self):
        r = Runner(rc=0)
        which = lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None
        changes = install.install(platform="darwin", runner=r, which=which)
        self.assertIn(["brew", "install", "terminal-notifier"], r.calls)
        self.assertTrue(any("terminal-notifier" in c for c in changes))

    def test_macos_terminal_notifier_dry_services_skipped(self):
        os.environ["FOREMAN_INSTALL_DRY_SERVICES"] = "1"
        self.addCleanup(os.environ.pop, "FOREMAN_INSTALL_DRY_SERVICES", None)
        r = Runner(rc=0)
        which = lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None
        changes = install.install(platform="darwin", runner=r, which=which)
        self.assertNotIn(["brew", "install", "terminal-notifier"], r.calls)
        self.assertTrue(any("brew install terminal-notifier" in c and "skipped" in c for c in changes))

    def test_macos_terminal_notifier_missing_and_no_brew(self):
        r = Runner(rc=0)
        changes = install.install(platform="darwin", runner=r, which=lambda b: None)
        self.assertNotIn(["brew", "install", "terminal-notifier"], r.calls)
        self.assertTrue(any("terminal-notifier" in c and "user step" in c.lower() for c in changes))

    def test_macos_terminal_notifier_already_present(self):
        r = Runner(rc=0)
        changes = install.install(platform="darwin", runner=r, which=lambda b: "/usr/local/bin/" + b)
        self.assertNotIn(["brew", "install", "terminal-notifier"], r.calls)
        self.assertFalse(any("terminal-notifier" in c for c in changes))

    def test_dry_services_skip_runner(self):
        os.environ["FOREMAN_INSTALL_DRY_SERVICES"] = "1"
        self.addCleanup(os.environ.pop, "FOREMAN_INSTALL_DRY_SERVICES", None)
        r = Runner()
        install.install(platform="linux", runner=r)
        self.assertEqual(r.calls, [])
        self.assertTrue(os.path.exists(os.path.join(self.home, ".config", "systemd", "user", "foreman.service")))
        install.uninstall(platform="linux", runner=r)
        self.assertEqual(r.calls, [])

    def test_no_statusline_is_left_alone(self):
        os.unlink(self.claude)
        os.unlink(self.agy)
        os.environ["FOREMAN_INSTALL_DRY_SERVICES"] = "1"
        self.addCleanup(os.environ.pop, "FOREMAN_INSTALL_DRY_SERVICES", None)
        install.install(platform="linux")
        self.assertFalse(os.path.exists(self.claude))
        self.assertFalse(os.path.exists(self.agy))

    def test_existing_regular_file_is_not_overwritten(self):
        link = os.path.join(self.home, ".local", "bin", "foreman")
        write(link, "#!/bin/sh\n")
        with self.assertRaises(install.InstallError):
            install.install(platform="linux", runner=Runner())
        self.assertEqual(open(link).read(), "#!/bin/sh\n")

    def test_purge_removes_state(self):
        os.environ["FOREMAN_INSTALL_DRY_SERVICES"] = "1"
        self.addCleanup(os.environ.pop, "FOREMAN_INSTALL_DRY_SERVICES", None)
        install.install(platform="linux")
        install.uninstall(purge=True, platform="linux")
        self.assertFalse(os.path.exists(self.foreman_home))

    def test_cli_install_output(self):
        env = {"FOREMAN_INSTALL_DRY_SERVICES": "1"}
        code, out, err = run_cli("install", "--machine-kind", "work", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("web pair", out)
        code, out, err = run_cli("install", "--machine-kind", "work", env=env)
        self.assertIn("already correct", out)
        code, out, err = run_cli("uninstall", env=env)
        self.assertEqual(code, 0, err)
        self.assertIn("removed", out)

    def test_install_says_what_upstream_mode_is_and_how_to_change_it(self):
        env = {"FOREMAN_INSTALL_DRY_SERVICES": "1"}
        for kind, expect in (("home", "auto"), ("work", "draft")):
            self.write_config({})
            code, out, err = run_cli("install", "--machine-kind", kind, env=env)
            self.assertEqual(code, 0, err)
            line = [l for l in out.splitlines() if l.startswith("upstream:")]
            self.assertEqual(len(line), 1, out)
            self.assertIn("upstream.mode=%s" % expect, line[0])
            self.assertIn("config.json", line[0])
            self.assertIn("draft", line[0])
            self.assertIn("off", line[0])
        # an explicit choice is reported as it is
        self.write_config({"upstream": {"mode": "off"}})
        code, out, err = run_cli("install", "--machine-kind", "home", env=env)
        self.assertIn("upstream.mode=off", out)

    def test_cli_uses_platform_param(self):
        r = Runner()
        os.environ["FOREMAN_INSTALL_DRY_SERVICES"] = "0"
        self.addCleanup(os.environ.pop, "FOREMAN_INSTALL_DRY_SERVICES", None)
        args = argparse.Namespace(machine_kind=None, bind=None, port=None, base_url=None)
        install_cmd.run(args, platform="darwin", runner=r)
        self.assertTrue(os.path.exists(
            os.path.join(self.home, "Library", "LaunchAgents", "com.foreman.serve.plist")))
        uninstall_cmd.run(argparse.Namespace(purge=False), platform="darwin", runner=r)


class StatuslineWrapTest(ForemanTestCase):
    """F2 (review 3c): the original statusline runs under `sh -c <quoted>`, so shell syntax in it works,
    stdin still reaches it, and unwrap reverses the wrapping exactly."""
    ORIGINALS = ['bash ~/sl.sh', 'FOO=hi sh -c "echo $FOO"', 'input=$(cat); echo "got:$input"',
                 'echo a; echo b', 'true && echo ok', "echo 'it''s' \"q\" café", 'a | b > /dev/null 2>&1']

    def test_wrap_unwrap_round_trip(self):
        for orig in self.ORIGINALS:
            wrapped = install.wrap_command(orig, "/x y/foreman", "claude")
            self.assertEqual(install.unwrap_command(wrapped), orig, orig)
            # idempotent: wrapping what is already wrapped (after unwrap) gives the same command
            self.assertEqual(install.wrap_command(install.unwrap_command(wrapped), "/x y/foreman", "claude"), wrapped)
        self.assertIsNone(install.unwrap_command("bash ~/sl.sh"))

    def test_legacy_unquoted_wrapper_is_still_unwrapped(self):
        self.assertEqual(install.unwrap_command("/b/foreman tap statusline --agent claude -- bash ~/sl.sh"),
                         "bash ~/sl.sh")

    def run_wrapped(self, orig, payload):
        import subprocess
        wrapped = install.wrap_command(orig, BIN, "claude")
        return subprocess.run(["sh", "-c", wrapped], input=payload, capture_output=True, text=True,
                              env=dict(os.environ), timeout=30)

    def test_shell_syntax_statuslines_run_and_get_stdin(self):
        payload = json.dumps({"session_id": "c-1", "rate_limits": {"five_hour": {"used_percentage": 42,
                              "resets_at": 4102444800}, "seven_day": {"used_percentage": 61, "resets_at": 4102444800}}})
        cases = {
            "FOO=hi sh -c 'echo $FOO'": "hi\n",
            'input=$(cat); echo "got:$input"': "got:%s\n" % payload,
            'echo a; echo b': "a\nb\n",
            'true && echo ok': "ok\n",
            'cat': payload,
        }
        for orig, expected in cases.items():
            r = self.run_wrapped(orig, payload)
            self.assertEqual((r.returncode, r.stdout), (0, expected), "%s -> %r" % (orig, r.stderr))
        from foreman import limits
        self.assertEqual(limits.load_provider_limits("claude")["seven_day"]["used_pct"], 61)  # tap still recorded

    def test_exit_status_of_the_original_is_kept(self):
        self.assertEqual(self.run_wrapped("exit 7", "{}").returncode, 7)
