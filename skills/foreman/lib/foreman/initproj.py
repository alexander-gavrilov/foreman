"""Project init (spec 5.4, 4.9 rule, architecture 4a/11). Idempotent: running it again changes nothing that is
already correct. Returns (changes, notes): `changes` are things this run altered, `notes` are information."""
import json
import os
import re
import shlex
import shutil

from foreman import install as machine
from foreman import paths, projects, sessions, store, telegram

CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "Notification",
                 "PermissionRequest", "SessionEnd")
CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest", "Stop",
                "SessionEnd")
TOOL_EVENTS = ("PreToolUse", "PostToolUse")
AGY_FLAT = ("SessionStart", "PreInvocation", "Stop")

# agent -> (binaries on PATH, config dirs under HOME)
AGENTS = {
    "claude": (("claude",), (".claude",)),
    "antigravity": (("agy", "antigravity"), (".gemini/antigravity-cli",)),
    "codex": (("codex",), (".codex",)),
}
HOOK_FILES = {"claude": ".claude/settings.local.json", "antigravity": ".agents/hooks.json",
              "codex": ".codex/hooks.json"}


class InitError(Exception):
    pass


def detect_agents(home=None, which=None):
    """Agents present on this machine: a binary on PATH or a config dir under HOME."""
    which = which or shutil.which
    home = machine.user_home(home)
    found = []
    for agent, (bins, dirs) in AGENTS.items():
        if any(which(b) for b in bins) or any(os.path.isdir(os.path.join(home, d)) for d in dirs):
            found.append(agent)
    return found


def _cmd(fm, agent, event):
    return "%s%s hook --agent %s --event %s" % (machine.env_prefix(), shlex.quote(fm), agent, event)


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        raise InitError("%s is not valid JSON; fix or remove it, then run init again" % path)
    if not isinstance(data, dict):
        raise InitError("%s does not hold a JSON object" % path)
    return data


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def merge_claude_layout(data, agent, events, fm):
    """Add/refresh foreman's handler for each event, keeping every other hook. Returns the new dict."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = data["hooks"] = {}
    for event in events:
        want = _cmd(fm, agent, event)
        tail = re.compile(r"hook --agent %s --event %s$" % (agent, event))
        groups = hooks.get(event)
        if not isinstance(groups, list):
            groups = hooks[event] = []
        found = False
        for group in groups:
            for h in (group.get("hooks") if isinstance(group, dict) else None) or []:
                if isinstance(h, dict) and tail.search(h.get("command") or "") and _is_foreman_handler(h):
                    found = True
                    h["command"] = want
        if not found:
            handler = {"type": "command", "command": want}
            if event != "SessionEnd":
                handler["timeout"] = 10
            group = {"hooks": [handler]}
            if event in TOOL_EVENTS:
                group = {"matcher": "*", "hooks": [handler]}
            groups.append(group)
    return data


def antigravity_hooks(fm):
    def handler(event):
        return {"type": "command", "command": _cmd(fm, "antigravity", event), "timeout": 10}
    entry = {"enabled": True}
    for event in AGY_FLAT[:1] + ("PreInvocation",) + AGY_FLAT[2:]:
        entry[event] = [handler(event)]
    for event in TOOL_EVENTS:
        entry[event] = [{"matcher": "*", "hooks": [handler(event)]}]
    return entry


def write_hooks(project_path, agent, fm):
    """Create/merge the project hook file for `agent`. Returns True when the file changed."""
    path = os.path.join(project_path, HOOK_FILES[agent])
    data = _load_json(path)
    before = json.dumps(data, sort_keys=True)
    data = data or {}
    if agent == "antigravity":
        data["foreman"] = antigravity_hooks(fm)
    else:
        merge_claude_layout(data, agent, CLAUDE_EVENTS if agent == "claude" else CODEX_EVENTS, fm)
    if os.path.exists(path) and json.dumps(data, sort_keys=True) == before:
        return False
    _save_json(path, data)
    return True


_FOREMAN_HANDLER = re.compile(r"\bhook --agent (?:claude|antigravity|codex) --event [A-Za-z]+$")


def _is_foreman_handler(h):
    """foreman's own hook command: `[VAR=value ...] <path>/foreman hook --agent A --event E`. Anchored to the
    executable's name, so another tool with the same arguments is never taken for ours (review 4 N11)."""
    cmd = h.get("command") if isinstance(h, dict) else None
    if not isinstance(cmd, str) or not _FOREMAN_HANDLER.search(cmd):
        return False
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words.pop(0)
    return bool(words) and os.path.basename(words[0]) == "foreman" and words[1:2] == ["hook"]


def strip_claude_layout(data):
    """Remove foreman's handlers (and the groups/events/`hooks` key they leave empty); the user's stay."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    changed = False
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            hs = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(hs, list) and any(_is_foreman_handler(h) for h in hs):
                changed = True
                group["hooks"] = [h for h in hs if not _is_foreman_handler(h)]
                if not group["hooks"]:
                    continue
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if changed and not hooks:
        del data["hooks"]
    return changed


def _has_foreman_hooks(project_path, agent):
    try:
        data = _load_json(os.path.join(project_path, HOOK_FILES[agent]))
    except InitError:
        return False
    if not data:
        return False
    if agent == "antigravity":
        return "foreman" in data
    return strip_claude_layout(data)  # works on the loaded copy only


def remove_hooks(project_path, agent):
    """Take foreman's handlers out of the project's hook file for `agent`; delete the file when nothing else is
    left in it. Returns True when something changed."""
    path = os.path.join(project_path, HOOK_FILES[agent])
    data = _load_json(path)
    if not data:
        return False
    if agent == "antigravity":
        changed = data.pop("foreman", None) is not None
    else:
        changed = strip_claude_layout(data)
    if not changed:
        return False
    if data:
        _save_json(path, data)
    else:
        os.unlink(path)
        try:
            os.rmdir(os.path.dirname(path))  # only if foreman's file was all that was in it
        except OSError:
            pass
    return True


def remove_project(path, home=None, in_worktree=False):
    """`foreman init --remove`: foreman's hook handlers out of the project's hook files (the user's stay) and the
    lines init added out of .git/info/exclude. Project state under FOREMAN_HOME is kept. Idempotent."""
    path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    owner = None if in_worktree else projects.worktree_project(path)
    if owner:  # the git exclude it would strip is shared with the main checkout; uninstall passes in_worktree
        raise InitError("%s is a worker worktree of project %s; run init --remove in %s"
                        % (path, owner["slug"], owner["path"]))
    changes = []
    if not in_worktree:
        # the trust entries foreman added for the project, and for its kept worktrees with no open session
        from foreman import sessions, trust
        for proj in projects.list_projects():
            if os.path.realpath(proj.get("path") or "") != path:
                continue
            busy = [(r.get("worktree") or {}).get("path") for r in sessions.list_sessions(proj["slug"])
                    if r.get("worktree") and sessions.is_open(r)]
            changes += trust.remove_project(proj["slug"], in_use=busy)
    for agent, rel in HOOK_FILES.items():
        if _has_foreman_hooks(path, agent) and is_tracked(path, rel):
            changes.append("left %s alone: it is tracked by git (remove the foreman hooks there yourself)" % rel)
            continue
        try:
            if remove_hooks(path, agent):
                changes.append("removed foreman hooks from %s" % rel)
        except InitError as exc:
            changes.append("left %s alone: %s" % (rel, exc))
    for rel in unexclude_from_git(path, list(HOOK_FILES.values())):
        changes.append("removed %s from .git/info/exclude" % rel)
    if not in_worktree:
        # the exclude lines are shared with the project's kept worker worktrees: take their hook files out too, or
        # they show up as untracked work for good and keep reporting
        for proj in projects.list_projects():
            if os.path.realpath(proj.get("path") or "") != path:
                continue
            wt_dir = paths.worktrees_dir(proj["slug"])
            for name in sorted(os.listdir(wt_dir)) if os.path.isdir(wt_dir) else []:
                try:
                    changes += ["worktree %s: %s" % (name, c)
                                for c in remove_project(os.path.join(wt_dir, name), home=home, in_worktree=True)]
                except Exception as exc:
                    changes.append("worktree %s: could not remove hooks (%s)" % (name, exc))
    return changes


# ------------------------------------------------------------ git exclude

def _exclude_file(project_path):
    git = os.path.join(project_path, ".git")
    if os.path.isfile(git):  # worktree / submodule: `gitdir: <path>`
        try:
            with open(git, encoding="utf-8") as f:
                line = f.read().strip()
        except OSError:
            return None
        if not line.startswith("gitdir:"):
            return None
        gitdir = line[len("gitdir:"):].strip()
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(project_path, gitdir)
        common = os.path.join(gitdir, "commondir")
        if os.path.exists(common):
            with open(common, encoding="utf-8") as f:
                gitdir = os.path.normpath(os.path.join(gitdir, f.read().strip()))
        return os.path.join(gitdir, "info", "exclude")
    if os.path.isdir(git):
        return os.path.join(git, "info", "exclude")
    return None


def is_tracked(project_path, rel):
    """True when git tracks `rel` (relative to the project): such a file is part of the repo and is not ours
    to edit."""
    import subprocess
    try:
        return subprocess.run(["git", "-C", project_path, "ls-files", "--error-unmatch", "--", rel],
                              capture_output=True, text=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _git_exclude(project_path):
    """(exclude file, prefix) for a folder inside a git work tree: git knows where info/exclude lives (also for
    worktrees) and how deep the folder is below the repo root, so entries can be anchored there. Falls back to
    looking for `.git` in the folder itself when git cannot say. (None, "") outside a repository."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", project_path, "rev-parse", "--show-toplevel", "--show-prefix",
                              "--git-path", "info/exclude"], capture_output=True, text=True, timeout=20)
        lines = out.stdout.split("\n")
        if out.returncode == 0 and len(lines) >= 3 and lines[2]:
            target = lines[2] if os.path.isabs(lines[2]) else os.path.join(project_path, lines[2])
            return os.path.normpath(target), lines[1]
    except (OSError, subprocess.SubprocessError):
        pass
    return _exclude_file(project_path), ""


def exclude_from_git(project_path, rel_paths):
    """Append missing entries to .git/info/exclude (never .gitignore), anchored below the repo root when the
    project is a subfolder. Returns the entries added (relative to the project)."""
    target, prefix = _git_exclude(project_path)
    if not target:
        return []
    try:
        with open(target, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    have = {ln.strip() for ln in text.splitlines()}
    missing = [r for r in rel_paths if "/" + prefix + r not in have and not (not prefix and r in have)]
    if not missing:
        return []
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "a", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        for r in missing:
            f.write("/" + prefix + r + "\n")
    return missing


def unexclude_from_git(project_path, rel_paths):
    """Drop the lines exclude_from_git added. Returns the entries removed."""
    target, prefix = _git_exclude(project_path)
    if not target:
        return []
    try:
        with open(target, encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        return []
    drop = {"/" + prefix + r for r in rel_paths}
    removed = [ln.strip()[1 + len(prefix):] for ln in lines if ln.strip() in drop]
    if removed:
        with open(target, "w", encoding="utf-8") as f:
            f.write("\n".join(ln for ln in lines if ln.strip() not in drop))
    return removed


# ------------------------------------------------------------ sessions

def available_transports():
    if os.environ.get("FOREMAN_FAKE_TRANSPORT"):
        from foreman.transports import get_transport
        return [get_transport("fake")]
    out = []
    for kind in ("orca", "herdr"):
        try:
            from foreman.transports import get_transport
            out.append(get_transport(kind))
        except Exception:
            continue
    return out


def _inside(cwd, root):
    if not cwd:
        return False
    cwd = os.path.realpath(cwd)
    return cwd == root or cwd.startswith(root.rstrip(os.sep) + os.sep)


def register_open_sessions(project, transports):
    """Agent sessions already open in the project's folder become owner=user workers (a plain shell terminal, where
    the transport detects no agent, is not a session). Returns their sids."""
    added = []
    for tr in transports:
        try:
            terms = tr.list()
        except Exception:
            continue
        for term in terms:
            in_project = _inside(term.cwd, project["path"]) or \
                (projects.worktree_project(term.cwd) or {}).get("slug") == project["slug"]
            if not term.alive or not term.agent or not in_project:
                continue
            if sessions.find_by_handle(term.handle, tr.kind):
                continue
            rec = sessions.create_session(
                project["slug"], term.agent, role="worker", owner="user", title=term.title or None,
                transport={"kind": tr.kind, "handle": term.handle}, cwd=term.cwd)
            added.append(rec["sid"])
    return added


# ------------------------------------------------------------ init

def init_project(path, name=None, home=None, agents=None, transports=None, fm=None, failures=None):
    """`failures`, when given, receives what init could not do (trust refused or not written); the caller exits with
    a failure status for it. Everything else init does is done regardless (spec 5.5)."""
    from foreman import trust
    path = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    if not os.path.isdir(path):
        raise InitError("%s is not a folder" % path)
    if trust.orchestrator_side():
        raise InitError("refused: `foreman init` writes agent trust, and an orchestrator or a worker it owns may not "
                        "trigger that; the user runs it (nothing was changed)")
    owner = projects.worktree_project(path)
    if owner:
        raise InitError("%s is a worker worktree of project %s; run init in %s" % (path, owner["slug"], owner["path"]))
    changes, notes = [], []
    fm = fm or machine.foreman_bin(home)

    known = any(p.get("path") == path for p in projects.list_projects())
    project = projects.create_project(path, name=name)
    if not known:
        changes.append("registered project %s" % project["slug"])
    elif name and project.get("name") != name:
        project["name"] = name
        projects.save_project(project)
        changes.append("renamed project %s to %r" % (project["slug"], name))

    agents = detect_agents(home) if agents is None else list(agents)
    tracked = {a for a, rel in HOOK_FILES.items() if is_tracked(path, rel)}
    # a tracked hook file is not ours to edit; every other one is excluded, even for an agent installed only later
    ours = [rel for a, rel in HOOK_FILES.items() if a not in tracked]
    for agent in agents:
        if agent in tracked:
            notes.append("%s is tracked by git, so foreman did not edit it; add the foreman hook there yourself "
                         "(`%s`) or untrack the file and run init again" % (HOOK_FILES[agent],
                                                                          _cmd(fm, agent, "<Event>")))
            continue
        if write_hooks(path, agent, fm):
            changes.append("wrote %s hooks (%s)" % (agent, HOOK_FILES[agent]))
    for rel in exclude_from_git(path, ours):
        changes.append("excluded %s from git (.git/info/exclude)" % rel)

    # the user's consent to trust this folder (spec 5.5): the narrow trust write for Antigravity and Codex
    res = trust.run_init(project, path, agents, fm)
    changes += res["changes"]
    notes += res["notes"]
    if failures is not None:
        failures += res["failures"]

    if project.get("telegram_topic_id") is None:
        try:
            topic = telegram.create_topic(project["name"])
        except Exception as exc:
            topic, _ = None, notes.append("Telegram topic not created: %s" % exc)
        if topic is not None:
            project["telegram_topic_id"] = topic
            projects.save_project(project)
            changes.append("created the Telegram topic")
        elif not any(n.startswith("Telegram topic") for n in notes):
            notes.append("no Telegram on this machine")

    trs = available_transports() if transports is None else transports
    for sid in register_open_sessions(project, trs):
        changes.append("registered the open session %s (owner user)" % sid)
    open_recs = [r for r in sessions.list_sessions(project["slug"]) if sessions.is_open(r)]
    if open_recs:
        notes.append("next: run `foreman orchestrator adopt <handle|sid>` to make an open session the orchestrator")
    return changes, notes
