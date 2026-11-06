"""Per-worker git worktrees: `foreman open` gives each orchestrator-owned worker its own checkout under
<state>/worktrees/<project>/<name> on a new branch foreman/<name>, so parallel workers never share one folder.
`foreman close` takes the checkout back when nothing in it would be lost. The commits always stay on the branch."""
import os
import re
import secrets
import subprocess

from foreman import initproj, install as machine, paths, sessions


class WorktreeError(Exception):
    pass


def _git(cwd, *args):
    try:
        res = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=60,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeError(str(exc))
    if res.returncode != 0:
        raise WorktreeError((res.stderr or res.stdout).strip() or "git %s failed" % args[0])
    return res.stdout.strip()


def is_repo_root(path):
    """True when `path` is the top folder of a git work tree that already has a commit."""
    try:
        top = _git(path, "rev-parse", "--show-toplevel")
        _git(path, "rev-parse", "--verify", "-q", "HEAD")
    except WorktreeError:
        return False
    return os.path.realpath(top) == os.path.realpath(path)


def create(project, title, agent):
    """New worktree + branch for a worker of `project`. Returns {"path", "branch"}; None when the project is not
    the root of a git repository (nothing to isolate); WorktreeError when git refuses."""
    if not is_repo_root(project["path"]):
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "worker").lower()).strip("-")[:30] or "worker"
    name = "%s-%s" % (slug, secrets.token_hex(2))
    path = os.path.join(os.path.realpath(paths.worktrees_dir(project["slug"])), name)
    branch = "foreman/" + name
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _git(project["path"], "worktree", "add", "-q", "-b", branch, path)
    try:
        # hook files are untracked (excluded from git), so a fresh checkout has none: write them, or the session
        # in it would be invisible to foreman. A tracked hook file arrives with the checkout.
        rel = initproj.HOOK_FILES.get(agent)
        if rel and not initproj.is_tracked(path, rel):
            initproj.exclude_from_git(project["path"], [rel])  # init did this; harmless if so (shared by worktrees)
            initproj.write_hooks(path, agent, machine.foreman_bin(None))
    except Exception as exc:
        remove(project["path"], path, branch)
        raise WorktreeError("hook files: %s" % exc)
    return {"path": path, "branch": branch}


def remove(project_path, path, branch):
    _git(project_path, "worktree", "remove", path)  # no --force: refuses a checkout with uncommitted work
    try:
        _git(project_path, "branch", "-d", branch)  # only when merged into the main checkout's HEAD
        return True
    except WorktreeError:
        return False


def release(rec):
    """Close time: remove the session's worktree if clean and not used by another open session. Returns a one-line
    result for the user, or None when the session has no worktree."""
    wt = rec.get("worktree")
    if not wt:
        return None
    path, branch = wt["path"], wt["branch"]
    if any(r["sid"] != rec["sid"] and sessions.is_open(r) and (r.get("worktree") or {}).get("path") == path
           for r in sessions.list_sessions(rec.get("project"))):
        return "kept worktree %s (still used by another open session)" % path
    if not os.path.isdir(path):
        return None
    from foreman import projects
    proj = projects.load_project(rec.get("project")) or {}
    try:
        gone = remove(proj.get("path") or "", path, branch)
    except WorktreeError as exc:
        why = "uncommitted work" if "untracked" in str(exc) or "modified" in str(exc) else str(exc).splitlines()[0]
        return "kept worktree %s (%s)" % (path, why)
    if gone:
        return "removed worktree %s" % path
    return "removed worktree %s, kept branch %s (commits not merged)" % (path, branch)
