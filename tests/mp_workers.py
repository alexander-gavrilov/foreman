"""Worker functions for multi-process tests. Module level so they work under the 'spawn' start method
(macOS default); FOREMAN_HOME is inherited from the parent's os.environ."""
import tests.helpers  # noqa: F401  (puts skills/foreman/lib on sys.path)


def run_in_processes(target, arglists, timeout=120):
    """Run target(*args) in one spawned process per arglist; returns the list of return values."""
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(arglists)) as pool:
        results = [pool.apply_async(target, args) for args in arglists]
        return [r.get(timeout) for r in results]


def increment(path, times):
    from foreman import store
    for _ in range(times):
        store.update_json(path, lambda d: dict(d, n=d["n"] + 1), default={})
    return True


def append_rows(path, tag, times):
    from foreman import store
    for i in range(times):
        store.append_jsonl(path, {"tag": tag, "i": i, "pad": "x" * 2000})
    return True


def claim_all(sid, batch):
    """Claim until the inbox is empty; returns the ids this process claimed."""
    from foreman import sessions
    mine = []
    while True:
        got = sessions.inbox_claim(sid, limit=batch)
        if not got:
            return mine
        mine.extend(m["id"] for m in got)


def create(path):
    from foreman import projects
    return projects.create_project(path)["slug"]
