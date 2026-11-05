"""Machine install / uninstall (spec 5.4, architecture 11). Everything lives under $HOME, so tests use a temp HOME.

install: ~/.local/bin/foreman symlink, config.json overrides, service unit (systemd user unit on Linux, launchd
agent on macOS), statusline taps. uninstall reverts exactly (state is kept unless purge=True).
Service-manager commands go through an injectable runner(argv) -> returncode and are skipped when
FOREMAN_INSTALL_DRY_SERVICES=1."""
import os
import re
import shlex
import shutil
import subprocess
import sys
from xml.sax.saxutils import escape

from foreman import config, paths, store

UNIT_NAME = "foreman.service"
PLIST_LABEL = "com.foreman.serve"
_WRAPPED = re.compile(r"\btap statusline --agent (\S+) -- ")
STATUSLINES = (("claude", (".claude", "settings.json")),
               ("antigravity", (".gemini", "antigravity-cli", "settings.json")))


class InstallError(Exception):
    pass


def skill_dir():
    """skills/foreman (the folder holding bin/, lib/, templates/)."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def launcher():
    return os.path.join(skill_dir(), "bin", "foreman")


def user_home(home=None):
    return home or os.path.expanduser("~")


def symlink_path(home=None):
    return os.path.join(user_home(home), ".local", "bin", "foreman")


def foreman_bin(home=None):
    """The absolute command hooks and units call: the ~/.local/bin symlink when it points at this launcher
    (stable across plugin updates), else the launcher itself."""
    link = symlink_path(home)
    try:
        if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(launcher()):
            return link
    except OSError:
        pass
    return launcher()


def default_runner(argv):
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=60).returncode
    except (OSError, subprocess.SubprocessError):
        return 127


def dry_services():
    return os.environ.get("FOREMAN_INSTALL_DRY_SERVICES") == "1"


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _write_if_changed(path, text):
    if _read(path) == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def _template(name):
    with open(os.path.join(skill_dir(), "templates", name), "r", encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------ statusline taps

def wrap_command(original, fm, agent):
    return "%s tap statusline --agent %s -- %s" % (shlex.quote(fm), agent, original)


def unwrap_command(command):
    """The original command inside a foreman tap wrapper, or None when `command` is not wrapped."""
    m = _WRAPPED.search(command or "")
    return command[m.end():] if m else None


def _statusline_file(home, parts):
    return os.path.join(user_home(home), *parts)


def _load_settings(path):
    import json
    text = _read(path)
    if text is None:
        return None, None
    try:
        data = json.loads(text)
    except ValueError:
        return None, text
    return (data if isinstance(data, dict) else None), text


def _swap_command(path, data, text, old, new):
    """Replace the statusLine command: a literal edit of the file text when the old value appears exactly once
    (keeps the user's formatting, so uninstall restores the file byte for byte), else a re-dump."""
    import json
    lit_old, lit_new = json.dumps(old, ensure_ascii=False), json.dumps(new, ensure_ascii=False)
    if text.count(lit_old) == 1:
        out = text.replace(lit_old, lit_new)
    else:
        data["statusLine"]["command"] = new
        out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)


def tap_statuslines(fm, home=None):
    changes = []
    for agent, parts in STATUSLINES:
        path = _statusline_file(home, parts)
        data, text = _load_settings(path)
        sl = (data or {}).get("statusLine")
        cmd = sl.get("command") if isinstance(sl, dict) else None
        if not cmd:
            continue
        original = unwrap_command(cmd)
        new = wrap_command(original if original is not None else cmd, fm, agent)
        if new == cmd:
            continue
        backup = path + ".foreman-bak"
        if original is None and not os.path.exists(backup):
            shutil.copyfile(path, backup)
        _swap_command(path, data, text, cmd, new)
        changes.append("tapped the %s statusline (%s)" % (agent, path))
    return changes


def untap_statuslines(home=None):
    changes = []
    for agent, parts in STATUSLINES:
        path = _statusline_file(home, parts)
        data, text = _load_settings(path)
        sl = (data or {}).get("statusLine")
        cmd = sl.get("command") if isinstance(sl, dict) else None
        original = unwrap_command(cmd) if cmd else None
        if original is None:
            continue
        _swap_command(path, data, text, cmd, original)
        backup = path + ".foreman-bak"
        if _read(backup) is not None and _load_settings(backup)[1] == _read(path):
            os.unlink(backup)
        changes.append("restored the %s statusline (%s)" % (agent, path))
    return changes


# ------------------------------------------------------------ service units

def _fill(template, fm, platform):
    quote = (lambda s: s.replace("%", "%%")) if platform != "darwin" else escape
    fmv = fm
    if platform != "darwin" and " " in fmv:
        fmv = '"%s"' % fmv
    return (template.replace("@FOREMAN@", quote(fmv)).replace("@FOREMAN_HOME@", quote(paths.home()))
            .replace("@PATH@", quote(os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))))


def unit_path(home=None, platform=None):
    if (platform or sys.platform) == "darwin":
        return os.path.join(user_home(home), "Library", "LaunchAgents", PLIST_LABEL + ".plist")
    return os.path.join(user_home(home), ".config", "systemd", "user", UNIT_NAME)


def install_service(fm, home=None, platform=None, runner=None):
    platform = platform or sys.platform
    run = runner or default_runner
    path = unit_path(home, platform)
    existed = os.path.exists(path)
    darwin = platform == "darwin"
    text = _fill(_template(PLIST_LABEL + ".plist" if darwin else UNIT_NAME), fm, platform)
    changed = _write_if_changed(path, text)
    out = ["wrote the service %s (%s)" % ("agent" if darwin else "unit", path)] if changed else []
    if dry_services():
        return out + ["service-manager commands skipped (FOREMAN_INSTALL_DRY_SERVICES=1)"]
    if darwin:
        target = "gui/%d" % os.getuid()
        loaded = run(["launchctl", "print", "%s/%s" % (target, PLIST_LABEL)]) == 0
        if changed and loaded:
            run(["launchctl", "bootout", "%s/%s" % (target, PLIST_LABEL)])
            loaded = False
        if not loaded:
            if run(["launchctl", "bootstrap", target, path]) == 0:
                out.append("started the service with launchctl")
            else:
                out.append("WARNING: launchctl bootstrap failed; start it with: launchctl bootstrap %s %s"
                           % (target, shlex.quote(path)))
    else:
        if changed:
            run(["systemctl", "--user", "daemon-reload"])
        ok = run(["systemctl", "--user", "enable", "--now", UNIT_NAME]) == 0
        if ok and changed and existed:
            run(["systemctl", "--user", "restart", UNIT_NAME])
        if not ok:
            out.append("WARNING: systemctl --user enable --now %s failed" % UNIT_NAME)
        elif changed:
            out.append("enabled and started the service with systemctl --user")
    return out


def uninstall_service(home=None, platform=None, runner=None):
    platform = platform or sys.platform
    run = runner or default_runner
    path = unit_path(home, platform)
    if not os.path.exists(path):
        return []
    if not dry_services():
        if platform == "darwin":
            run(["launchctl", "bootout", "gui/%d/%s" % (os.getuid(), PLIST_LABEL)])
        else:
            run(["systemctl", "--user", "disable", "--now", UNIT_NAME])
    os.unlink(path)
    if platform != "darwin" and not dry_services():
        run(["systemctl", "--user", "daemon-reload"])
    return ["removed the service (%s)" % path]


# ------------------------------------------------------------ install / uninstall

def install(machine_kind=None, bind=None, port=None, base_url=None, home=None, platform=None, runner=None):
    """Returns the list of changes made (empty when everything was already in place)."""
    platform = platform or sys.platform
    changes = []
    link = symlink_path(home)
    target = launcher()
    if os.path.islink(link) and os.readlink(link) == target:
        pass
    elif os.path.lexists(link) and not os.path.islink(link):
        raise InstallError("%s exists and is not a symlink; move it away first" % link)
    else:
        os.makedirs(os.path.dirname(link), exist_ok=True)
        if os.path.islink(link):
            os.unlink(link)
        os.symlink(target, link)
        changes.append("linked %s -> %s" % (link, target))
    fm = foreman_bin(home)

    overrides = {}
    if machine_kind:
        overrides.setdefault("machine", {})["kind"] = machine_kind
    if bind:
        overrides.setdefault("web", {})["bind"] = list(bind)
    if port:
        overrides.setdefault("web", {})["port"] = int(port)
    if base_url:
        overrides.setdefault("web", {})["base_url"] = base_url
    current = store.read_json(paths.config_file(), {})
    current = current if isinstance(current, dict) else {}
    merged = config.deep_merge(current, overrides)
    if overrides and merged != current or not os.path.exists(paths.config_file()):
        store.write_json(paths.config_file(), merged)
        changes.append("wrote %s" % paths.config_file())

    changes += install_service(fm, home, platform, runner)
    changes += tap_statuslines(fm, home)
    return changes


def uninstall(purge=False, home=None, platform=None, runner=None):
    changes = untap_statuslines(home)
    changes += uninstall_service(home, platform, runner)
    link = symlink_path(home)
    if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(launcher()):
        os.unlink(link)
        changes.append("removed %s" % link)
    if purge:
        root = paths.home()
        if root in ("", os.sep, user_home(home)):
            raise InstallError("refusing to purge %s" % root)
        if os.path.isdir(root):
            shutil.rmtree(root)
            changes.append("deleted the state folder %s" % root)
    return changes
