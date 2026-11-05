"""Spec §8.11 — the same install works on Linux and macOS.

Two layers, both in a temporary HOME:
  * HostInstallTest drives the real CLI (`foreman install / init / uninstall`) on the machine running the suite and
    then EXECUTES what install wrote: the symlink, the statusline wrapper and the project hook command line.
    systemctl / launchctl / brew are shims that only record their argv.
  * BranchesTest runs the install code once per platform branch (linux, darwin) with an injected command runner,
    so the macOS branch (LaunchAgent, launchctl, terminal-notifier) is verified on a Linux box too.
The phrase "установи плагин" (an agent following SKILL.md) and a real Mac are user-only checks (see README)."""
import json
import os
import subprocess
import sys
import unittest

from tests.acceptance.common import read_json, read_text, AcceptanceCase
from tests.helpers import BIN, ROOT


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    if mode:
        os.chmod(path, mode)


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = os.readlink(p) if os.path.islink(p) else read_text(p)
    return out


class Runner:
    """Records service-manager / brew command lines; `launchctl print` says 'not loaded'."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv):
        self.calls.append(list(argv))
        return 1 if argv[:2] == ["launchctl", "print"] else 0


class HostInstallTest(AcceptanceCase):
    def setUp(self):
        super().setUp()
        self.claude_settings = os.path.join(self.home, ".claude", "settings.json")
        self.agy_settings = os.path.join(self.home, ".gemini", "antigravity-cli", "settings.json")
        self.sl_log = os.path.join(self.tmp, "statusline-ran.txt")
        write(os.path.join(self.tmp, "sl.sh"), "#!/bin/sh\ncat >/dev/null\necho 'MY STATUS LINE'\n", 0o755)
        write(self.claude_settings, json.dumps({"theme": "dark", "statusLine": {
            "type": "command", "command": "sh %s" % os.path.join(self.tmp, "sl.sh")}}, indent=2) + "\n")
        write(self.agy_settings, json.dumps({"statusLine": {
            "type": "command", "command": "sh %s" % os.path.join(self.tmp, "sl.sh")}}, indent=2) + "\n")
        self.state_before = snapshot(self.home)
        self.link = os.path.join(self.home, ".local", "bin", "foreman")
        if sys.platform == "darwin":
            self.unit = os.path.join(self.home, "Library", "LaunchAgents", "com.foreman.serve.plist")
        else:
            self.unit = os.path.join(self.home, ".config", "systemd", "user", "foreman.service")

    def test_install_then_use_then_uninstall_round_trip(self):
        # install
        out = self.fm_ok("install", "--machine-kind", "home", "--base-url", "http://ghome.lan:8765")
        self.assertIn("linked", out)
        self.assertIn("pair a browser or phone with", out)
        self.assertEqual(os.readlink(self.link), BIN)
        unit = read_text(self.unit)
        self.assertIn(self.link, unit)
        self.assertIn(self.foreman_home, unit)
        service_calls = [c["argv"] for c in self.shim_calls("systemctl", "launchctl")]
        self.assertTrue(service_calls, "the service manager was never asked to start foreman")
        cfg = self.load_config()
        self.assertEqual((cfg["machine"]["kind"], cfg["web"]["base_url"]), ("home", "http://ghome.lan:8765"))
        self.assertIn("tap statusline --agent claude --", read_json(self.claude_settings)["statusLine"]["command"])

        # install again: nothing changes
        again = self.fm_ok("install", "--machine-kind", "home", "--base-url", "http://ghome.lan:8765")
        self.assertIn("already correct", again)

        # the installed symlink is a working `foreman`
        version = subprocess.run([sys.executable, self.link, "--version"], capture_output=True, text=True)
        self.assertEqual(version.returncode, 0)
        self.assertTrue(version.stdout.startswith("foreman "))

        # the wrapped statusline still prints the user's own line and records the limits on the way
        wrapped = read_json(self.claude_settings)["statusLine"]["command"]
        proc = subprocess.run(["sh", "-c", wrapped], input=json.dumps(self.claude_statusline(weekly=57)),
                              capture_output=True, text=True, env=self.env())
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "MY STATUS LINE"), proc.stderr)
        self.assertEqual(self.fm_json("limits", "--json")["claude"]["windows"]["seven_day"]["used_pct"], 57)

        # a project set up afterwards gets hooks that call the installed foreman by absolute path, and they work
        slug, path = self.make_project("demo")
        hooks = read_json(os.path.join(path, ".claude", "settings.local.json"))["hooks"]
        command = hooks["SessionStart"][0]["hooks"][0]["command"]
        self.assertTrue(command.startswith(self.link), command)
        proc = subprocess.run(["sh", "-c", command], input=json.dumps(
            {"hook_event_name": "SessionStart", "session_id": "c-1", "cwd": path, "source": "startup"}),
            capture_output=True, text=True, env=self.env())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("foreman project rule", json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"])

        # uninstall restores the machine; the state is kept
        out = self.fm_ok("uninstall")
        self.assertIn("restored the claude statusline", out)
        self.assertFalse(os.path.lexists(self.link))
        self.assertFalse(os.path.exists(self.unit))
        self.assertTrue(os.path.isdir(self.foreman_home))
        for settings, original in ((self.claude_settings, "claude"), (self.agy_settings, "agy")):
            self.assertNotIn("tap statusline", read_text(settings), original)
        after = snapshot(self.home)
        for rel, text in self.state_before.items():
            self.assertEqual(after.get(rel), text, "%s was not restored byte for byte" % rel)
        # and the project's hook file (written by init, not by install) is the user's to keep
        self.assertIn("nothing to remove", self.fm_ok("uninstall"))

        # purge is the only thing that removes the state
        self.fm_ok("uninstall", "--purge")
        self.assertFalse(os.path.exists(self.foreman_home))

    def test_install_refuses_to_replace_a_real_file_at_the_symlink_path(self):
        write(self.link, "#!/bin/sh\necho mine\n", 0o755)
        code, out, err = self.fm("install", "--machine-kind", "home")
        self.assertNotEqual(code, 0)
        self.assertIn("is not a symlink", err)
        self.assertEqual(read_text(self.link), "#!/bin/sh\necho mine\n")

    def test_the_install_instructions_agents_follow_exist(self):
        skill = read_text(os.path.join(ROOT, "skills", "foreman", "SKILL.md"))
        self.assertIn("установи плагин", skill)
        self.assertIn("foreman install", skill)
        for rel in ("skills/foreman/templates/foreman.service", "skills/foreman/templates/com.foreman.serve.plist",
                    ".claude-plugin/plugin.json"):
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)


class BranchesTest(AcceptanceCase):
    """Each platform branch of `foreman install`, in a fresh HOME, with the service manager faked."""

    def run_branch(self, platform, which=None):
        from foreman import install
        runner = Runner()
        before = snapshot(self.home)
        changes = install.install(machine_kind="work" if platform == "darwin" else "home", platform=platform,
                                  runner=runner, which=which or (lambda name: None))
        return before, changes, runner

    def setUp(self):
        super().setUp()
        os.environ.pop("FOREMAN_INSTALL_DRY_SERVICES", None)

    def test_linux_branch_installs_a_systemd_user_unit(self):
        before, changes, runner = self.run_branch("linux")
        unit = os.path.join(self.home, ".config", "systemd", "user", "foreman.service")
        text = read_text(unit)
        self.assertIn("ExecStart=%s serve" % os.path.join(self.home, ".local", "bin", "foreman"), text)
        self.assertIn("Restart=always", text)
        self.assertIn(self.foreman_home, text)
        self.assertEqual(runner.calls, [["systemctl", "--user", "daemon-reload"],
                                        ["systemctl", "--user", "enable", "--now", "foreman.service"]])
        self.assertFalse(os.path.exists(os.path.join(self.home, "Library")))
        self.assertTrue(any("linked" in c for c in changes))
        # idempotent: a second run changes nothing and starts nothing
        from foreman import install
        runner2 = Runner()
        self.assertEqual([c for c in install.install(machine_kind="home", platform="linux", runner=runner2)
                          if "linked" in c or "wrote" in c], [])
        # uninstall reverts exactly
        runner3 = Runner()
        install.uninstall(platform="linux", runner=runner3)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(runner3.calls[0], ["systemctl", "--user", "disable", "--now", "foreman.service"])

    def test_darwin_branch_installs_a_launch_agent_and_terminal_notifier(self):
        before, changes, runner = self.run_branch("darwin", which=lambda name: "/opt/homebrew/bin/brew"
                                                  if name == "brew" else None)
        plist = os.path.join(self.home, "Library", "LaunchAgents", "com.foreman.serve.plist")
        text = read_text(plist)
        self.assertIn(os.path.join(self.home, ".local", "bin", "foreman"), text)
        self.assertIn("<key>KeepAlive</key>", text)
        self.assertIn(self.foreman_home, text)
        verbs = [c[:2] for c in runner.calls]
        self.assertIn(["launchctl", "bootstrap"], verbs)
        self.assertIn(["brew", "install"], verbs)
        self.assertEqual(runner.calls[-1], ["brew", "install", "terminal-notifier"])
        self.assertFalse(os.path.exists(os.path.join(self.home, ".config", "systemd")))
        self.assertTrue(any("terminal-notifier" in c for c in changes))
        from foreman import install
        install.uninstall(platform="darwin", runner=Runner())
        self.assertEqual(snapshot(self.home), before)

    def test_darwin_without_homebrew_leaves_a_user_step_instead_of_failing(self):
        before, changes, runner = self.run_branch("darwin", which=lambda name: None)
        self.assertTrue(any(c.startswith("user step: install terminal-notifier") for c in changes), changes)
        self.assertNotIn("brew", [c[0] for c in runner.calls])

    def test_darwin_with_terminal_notifier_present_installs_nothing_extra(self):
        before, changes, runner = self.run_branch("darwin", which=lambda name: "/usr/local/bin/" + name)
        self.assertNotIn("brew", [c[0] for c in runner.calls])

    def test_the_two_branches_leave_the_same_shared_pieces(self):
        """Symlink, config and statusline taps are platform-independent; only the service files differ."""
        for platform in ("linux", "darwin"):
            with self.subTest(platform=platform):
                from foreman import install
                write(os.path.join(self.home, ".claude", "settings.json"),
                      json.dumps({"statusLine": {"type": "command", "command": "bash ~/sl.sh"}}))
                install.install(platform=platform, runner=Runner(), which=lambda n: "/x/" + n)
                link = os.path.join(self.home, ".local", "bin", "foreman")
                self.assertEqual(os.readlink(link), BIN)
                cmd = read_json(os.path.join(self.home, ".claude", "settings.json"))["statusLine"]["command"]
                self.assertTrue(cmd.endswith("tap statusline --agent claude -- bash ~/sl.sh"), cmd)
                install.uninstall(platform=platform, runner=Runner())
                self.assertFalse(os.path.lexists(link))


if __name__ == "__main__":
    unittest.main()
