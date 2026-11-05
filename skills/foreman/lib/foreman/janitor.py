"""Janitor for idle work folders (architecture §11, spec §4.9).

Announce folders idle >= TTL-1 days (journal line + notify.notify(None, ...)).
Delete folders idle >= TTL days only if announced earlier.
Deletion never follows symlinks out of agent-work.
"""
import os
import shutil

from foreman import config, notify, paths, projects, timeutil


def scan_work_folders():
    """Find all work folders under paths.agent_work_root() with their newest mtime inside."""
    root = paths.agent_work_root()
    results = []
    if not os.path.isdir(root):
        return results

    try:
        slugs = os.listdir(root)
    except OSError:
        return results

    now_ts = timeutil.now().timestamp()

    for slug in sorted(slugs):
        proj_dir = os.path.join(root, slug)
        if not os.path.isdir(proj_dir):
            continue
        try:
            folder_names = os.listdir(proj_dir)
        except OSError:
            continue

        for fname in sorted(folder_names):
            folder_path = os.path.join(proj_dir, fname)
            if not os.path.isdir(folder_path):
                continue

            # Calculate newest mtime inside
            try:
                newest = os.path.getmtime(folder_path)
            except OSError:
                newest = 0

            for cur_root, dirs, files in os.walk(folder_path, followlinks=False):
                for f in files:
                    p = os.path.join(cur_root, f)
                    try:
                        m = os.path.getmtime(p)
                        if m > newest:
                            newest = m
                    except OSError:
                        pass
                for d in dirs:
                    p = os.path.join(cur_root, d)
                    try:
                        m = os.path.getmtime(p)
                        if m > newest:
                            newest = m
                    except OSError:
                        pass

            idle_seconds = max(0, now_ts - newest)
            idle_days = idle_seconds / 86400.0
            results.append({
                "slug": slug,
                "name": fname,
                "path": folder_path,
                "newest_mtime": newest,
                "idle_days": idle_days,
            })
    return results


def safe_delete_dir(path):
    """Safely delete path, ensuring it is inside agent-work and never following symlinks out."""
    real_path = os.path.realpath(path)
    root = os.path.realpath(paths.agent_work_root())
    if not real_path.startswith(root + os.sep):
        raise ValueError("refusing to delete path outside agent-work: %r" % (path,))

    if os.path.islink(path):
        os.unlink(path)
        return

    # Bottom-up deletion without following symlinks
    for cur_root, dirs, files in os.walk(path, topdown=False, followlinks=False):
        for f in files:
            p = os.path.join(cur_root, f)
            try:
                os.unlink(p)
            except OSError:
                pass
        for d in dirs:
            p = os.path.join(cur_root, d)
            try:
                if os.path.islink(p):
                    os.unlink(p)
                else:
                    os.rmdir(p)
            except OSError:
                pass

    try:
        if os.path.islink(path):
            os.unlink(path)
        else:
            os.rmdir(path)
    except OSError:
        pass


def janitor_pass(watch_state):
    """Run one janitor check pass over all work folders."""
    ttl_days = config.get("thresholds.work_ttl_days", 14)
    announce_days = max(0, ttl_days - 1)

    janitor_state = watch_state.setdefault("janitor", {})
    folders = scan_work_folders()

    def announce(item, rec_idle):
        projects.journal_append(
            item["slug"],
            actor="foreman",
            kind="clean",
            text="Work folder idle for %d days scheduled for deletion: %s" % (int(rec_idle), item["name"]),
        )
        notify.notify(None, "Work folder idle: %s" % item["path"])
        janitor_state[item["path"]] = {
            "announced_at": timeutil.now_iso(),
            "announced_mtime": item["newest_mtime"],
            "idle_days": rec_idle,
        }

    for item in folders:
        path = item["path"]
        idle_days = item["idle_days"]

        rec = janitor_state.get(path)
        if idle_days < announce_days:
            # Used again since it was announced: the old announcement no longer covers it.
            janitor_state.pop(path, None)
        elif idle_days >= ttl_days:
            announced_mtime = (rec or {}).get("announced_mtime")
            untouched = announced_mtime is not None and item["newest_mtime"] <= announced_mtime
            if rec and rec.get("announced_at") and untouched:
                # Delete at TTL only if announced earlier and nothing was touched since that listing
                safe_delete_dir(path)
                janitor_state.pop(path, None)
            else:
                announce(item, idle_days)
        elif not rec or not rec.get("announced_at"):
            announce(item, idle_days)

    # Clean up state entries for paths that no longer exist
    existing_paths = {f["path"] for f in folders}
    for tracked in list(janitor_state.keys()):
        if tracked not in existing_paths:
            janitor_state.pop(tracked, None)
