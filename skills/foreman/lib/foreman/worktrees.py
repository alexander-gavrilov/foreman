"""Per-worker git worktrees: `foreman open` gives each orchestrator-owned worker its own checkout under
<state>/worktrees/<project>/<name> on a new branch foreman-<name>, so parallel workers never share one folder.
`foreman close` (or the watch loop's sweep, for a session that ended on its own) takes the checkout back when
nothing in it would be lost. Commits stay on the branch."""
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


def applicable(path):
    """"root" when `path` is the top folder of a git work tree with a commit (a worktree can be made),
    "subfolder" when it is inside one, else None (not a repository: nothing to isolate)."""
    try:
        top = _git(path, "rev-parse", "--show-toplevel")
        _git(path, "rev-parse", "--verify", "-q", "HEAD")
    except WorktreeError:
        return None
    return "root" if os.path.realpath(top) == os.path.realpath(path) else "subfolder"


def write_hook_files(project_path, dest, agent):
    """Hook files are untracked (excluded from git), so a fresh checkout has none; without them a session in it
    would be invisible to foreman. Written for `agent` and for every agent the project itself has hooks for
    (a rotation may change the agent, and a session may be started by hand). A tracked file arrives with the
    checkout and is not ours to edit."""
    fm = machine.foreman_bin(None)
    for name, rel in initproj.HOOK_FILES.items():
        if name != agent and not initproj._has_foreman_hooks(project_path, name):
            continue
        if initproj.is_tracked(dest, rel):
            continue
        initproj.exclude_from_git(project_path, [rel])  # init did this; harmless if so (shared by worktrees)
        initproj.write_hooks(dest, name, fm)


def create(project, title, agent):
    """New worktree + branch for a worker of `project` (a git repo root). Returns {"path", "branch"}.
    WorktreeError when anything fails; nothing is left behind then."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "worker").lower()).strip("-")[:30] or "worker"
    root = os.path.realpath(paths.worktrees_dir(project["slug"]))
    os.makedirs(root, exist_ok=True)
    path, branch = _claim(project["path"], root, slug)
    # From here on the folder and the branch name are ours alone, so cleaning up after a failure cannot touch
    # anything that belongs to another worker, or a branch kept from an earlier one.
    try:
        _git(project["path"], "worktree", "add", "-q", "-b", branch, path)
        write_hook_files(project["path"], path, agent)
    except Exception as exc:
        discard(project["path"], path, branch)
        raise WorktreeError(str(exc).strip().splitlines()[0] if str(exc).strip() else "git failed")
    return {"path": path, "branch": branch}


def _claim(project_path, root, slug, attempts=20):
    """Pick a worktree name nobody uses: neither a folder under `root` nor a branch of that name (a branch kept from
    a closed session counts). The folder is made here, atomically, so two opens racing for one name cannot both
    get it; `git worktree add` accepts the empty folder. Returns (path, branch)."""
    for _ in range(attempts):
        name = "%s-%s" % (slug, secrets.token_hex(2))
        path, branch = os.path.join(root, name), "foreman-" + name
        try:
            os.mkdir(path)
        except FileExistsError:
            continue
        except OSError as exc:
            raise WorktreeError(str(exc))
        try:
            _git(project_path, "rev-parse", "--verify", "-q", "refs/heads/" + branch)
        except WorktreeError:  # no such branch: the name is free
            return path, branch
        os.rmdir(path)
    raise WorktreeError("no free worktree name for %r after %d tries" % (slug, attempts))


def discard(project_path, path, branch):
    """Best effort: drop a worktree that was never used, whatever state it is in."""
    for args in (("worktree", "remove", "--force", path), ("worktree", "prune"), ("branch", "-D", branch)):
        try:
            _git(project_path, *args)
        except WorktreeError:
            pass


def _inside(path, root):
    path, root = os.path.realpath(path or ""), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def release(rec):
    """Remove the session's worktree if nothing would be lost. Returns a one-line result for the user, or None
    when the session has no worktree (or it is already gone). What is lost with the folder: files git ignores
    (caches, a .env). What is kept: uncommitted changes, commits on no branch, and a folder another open session
    still works in (by its record or by its cwd). The branch stays unless git finds it merged."""
    wt = rec.get("worktree")
    if not wt or not os.path.isdir(wt["path"]):
        return None
    path, branch = wt["path"], wt["branch"]
    if any(r["sid"] != rec["sid"] and sessions.is_open(r) and
           ((r.get("worktree") or {}).get("path") == path or _inside(r.get("cwd"), path))
           for r in sessions.list_sessions(rec.get("project"))):
        return "kept worktree %s (still used by another open session)" % path
    from foreman import projects
    base = (projects.load_project(rec.get("project")) or {}).get("path") or ""
    try:
        if _git(path, "status", "--porcelain"):
            return "kept worktree %s (uncommitted work)" % path
        if _git(path, "rev-list", "-n1", "HEAD", "--not", "--branches", "--tags", "--remotes"):
            return "kept worktree %s (HEAD has commits on no branch)" % path
        _git(base, "worktree", "remove", path)  # no --force
    except WorktreeError as exc:
        return "kept worktree %s (%s)" % (path, (str(exc).splitlines() or ["git failed"])[0])
    try:
        _git(base, "branch", "-d", branch)  # only when merged into the main checkout's HEAD
    except WorktreeError:
        return "removed worktree %s, kept branch %s (not merged)" % (path, branch)
    return "removed worktree %s" % path


def sweep():
    """Release the worktrees of sessions that ended without `foreman close` (the agent exited on its own) or whose
    kept worktree has since been cleaned up. Returns the result lines."""
    by_path, lines = {}, []
    for rec in sessions.list_sessions():
        if rec.get("worktree"):
            by_path.setdefault(rec["worktree"]["path"], []).append(rec)
    for path, recs in by_path.items():
        if any(sessions.is_open(r) for r in recs):
            continue
        line = release(recs[-1])
        if line:
            lines.append(line)
    return lines
