"""Per-worker git worktrees: `foreman open` gives each orchestrator-owned worker its own checkout under
<state>/worktrees/<project>/<name> on a new branch foreman-<name>, so parallel workers never share one folder.
`foreman close` (or the watch loop's sweep, for a session that ended on its own) takes the checkout back when
nothing in it would be lost. Commits stay on the branch."""
import os
import re
import secrets
import shutil
import subprocess
import time

from foreman import initproj, install as machine, paths, sessions


class WorktreeError(Exception):
    pass


ORPHAN_AGE_S = 600  # a worktree no session claims is taken back once it is this old (an open takes < 2 min)
REMOVE_TIMEOUT_S = 600  # removing a big tree (node_modules) must not be killed half way


def _git(cwd, *args, timeout=60):
    try:
        res = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, timeout=timeout,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WorktreeError(str(exc))
    if res.returncode != 0:
        raise WorktreeError((res.stderr or res.stdout).strip() or "git %s failed" % args[0])
    return res.stdout.strip()


def applicable(path):
    """"root" when `path` is the top folder of a git work tree with a commit (a worktree can be made),
    "subfolder" when it is inside one, "nocommit" when it is a repository without a commit yet, else None (not a
    repository: nothing to isolate)."""
    try:
        top = _git(path, "rev-parse", "--show-toplevel")
    except WorktreeError:
        return None
    try:
        _git(path, "rev-parse", "--verify", "-q", "HEAD")
    except WorktreeError:
        return "nocommit"
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
    """Best effort: drop a worktree this call made and nothing has used, whatever state it is in: the git
    registration (only this path's), the branch and the folder itself. No `worktree prune`: that would drop the
    registration of any other worktree whose folder happens to be missing right now."""
    for args in (("worktree", "remove", "--force", path), ("branch", "-D", branch)):
        try:
            _git(project_path, *args, timeout=REMOVE_TIMEOUT_S)
        except WorktreeError:
            pass
    if os.path.lexists(path):  # git never registered it (a failed `worktree add` leaves the claimed folder)
        shutil.rmtree(path, ignore_errors=True)


def _inside(path, root):
    path, root = os.path.realpath(path or ""), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def _in_use(path, project, except_sid=None):
    """Another open session works in `path` (by its record or by its cwd)."""
    return any(r["sid"] != except_sid and sessions.is_open(r) and
               ((r.get("worktree") or {}).get("path") == path or _inside(r.get("cwd"), path))
               for r in sessions.list_sessions(project))


def _remove_if_safe(base, path, branch):
    """Take the checkout back unless something would be lost; returns the one-line result. Lost with the folder:
    files git ignores. Kept: uncommitted changes (new files included), commits no branch, tag or remote branch
    holds. The branch goes only if it is merged into the branch the main checkout is on."""
    try:
        if _git(path, "status", "--porcelain"):
            return "kept worktree %s (uncommitted work)" % path
        if _git(path, "rev-list", "-n1", "HEAD", "--not", "--branches", "--tags", "--remotes"):
            return "kept worktree %s (HEAD has commits on no branch)" % path
        _git(base, "worktree", "remove", path, timeout=REMOVE_TIMEOUT_S)  # no --force
    except WorktreeError as exc:
        return "kept worktree %s (%s)" % (path, (str(exc).splitlines() or ["git failed"])[0])
    try:
        # `git branch -d` alone also accepts a branch merged into its upstream (pushed, not merged here), so the
        # target is checked explicitly: the branch the main checkout is on
        _git(base, "symbolic-ref", "-q", "HEAD")  # a detached HEAD is no branch: nothing counts as merged into it
        _git(base, "merge-base", "--is-ancestor", branch, "HEAD")
        _git(base, "branch", "-d", branch)
    except WorktreeError:
        return "removed worktree %s, kept branch %s (not merged)" % (path, branch)
    return "removed worktree %s" % path


def release(rec):
    """Remove the session's worktree if nothing would be lost. Returns a one-line result for the user, or None
    when the session has no worktree (or it is already gone). See `_remove_if_safe` for what goes and what stays;
    a folder another open session still works in is kept."""
    wt = rec.get("worktree")
    if not wt or not os.path.isdir(wt["path"]):
        return None
    path, branch = wt["path"], wt["branch"]
    if _in_use(path, rec.get("project"), except_sid=rec["sid"]):
        return "kept worktree %s (still used by another open session)" % path
    from foreman import projects
    base = (projects.load_project(rec.get("project")) or {}).get("path") or ""
    if not base:  # `git -C ""` would act on whatever repository the foreman process is in
        return "kept worktree %s (its project is unknown)" % path
    return _remove_if_safe(base, path, branch)


def _reclaim_orphan(project, path, now):
    """A folder under the project's worktrees that no session record claims: the `open` that made it was killed
    (SIGTERM, a closed terminal) before it recorded the session. Left alone while young."""
    try:
        if now - os.path.getmtime(path) < ORPHAN_AGE_S:
            return None
    except OSError:
        return None
    if _in_use(path, project["slug"]):
        return None
    if not os.path.lexists(os.path.join(path, ".git")):  # git never got to register it
        try:
            os.rmdir(path)  # only when empty
            return "removed empty worktree folder %s (no session)" % path
        except OSError:
            return None
    return _remove_if_safe(project["path"], path, "foreman-" + os.path.basename(path))


def _isolated(fn, *args, label):
    """One item's failure must not stop the sweep of the others; it becomes a line instead."""
    try:
        return fn(*args)
    except Exception as exc:
        return "could not clean up %s (%s)" % (label, exc)


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
        line = _isolated(release, recs[-1], label=path)
        if line:
            lines.append(line)
    from foreman import projects
    now = time.time()
    for proj in projects.list_projects():
        wt_dir = paths.worktrees_dir(proj["slug"])
        if not os.path.isdir(wt_dir):
            continue
        for name in sorted(os.listdir(wt_dir)):
            path = os.path.realpath(os.path.join(wt_dir, name))
            if path in by_path or not os.path.isdir(path):
                continue
            line = _isolated(_reclaim_orphan, proj, path, now, label=path)
            if line:
                lines.append(line)
    return lines
