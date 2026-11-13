"""foreman status: one line per session, grouped by project (spec §5.1)."""
from foreman import config, projects, sessions, timeutil
from foreman.cliutil import CliError, add_project_arg, dump_json



def register(subparsers):
    p = subparsers.add_parser("status", help="sessions, their state, context and owner")
    add_project_arg(p)
    p.add_argument("--all", action="store_true", help="every project (default: the one containing the cwd)")
    p.add_argument("--json", action="store_true")


def format_context(tokens, soft):
    if tokens is None:
        return "unknown"
    text = str(tokens) if tokens < 1000 else "%dk" % round(tokens / 1000)
    return text + (" ⚠" if soft is not None and tokens > soft else "")


def _select_projects(args):
    if args.project:
        proj = projects.load_project(args.project)
        if not proj:
            raise CliError("unknown project %r" % args.project)
        return [proj]
    if not args.all:
        import os
        here = projects.project_for_path(os.getcwd())
        if here:
            return [here]
    return projects.list_projects()


def _state_text(rec, now):
    text = "%s %s" % (rec.get("state"), timeutil.age_text(rec.get("state_since"), now=now))
    if rec.get("lifecycle") == "rotated":
        text += (" · rotated → %s" % rec["replaced_by"]) if rec.get("replaced_by") else " · rotated"
    return text


def _trust_drift(slug):
    """Trust that the scheduled check found not working (spec 5.5), as it last reported it."""
    from foreman import paths, store
    try:
        state = store.read_json(paths.watch_state_file(), {})
        rows = ((state.get("trust_check") or {}).get(slug) or {}).get("reported") or []
    except Exception:
        return []
    return [r for r in rows if isinstance(r, str)]


def run(args):
    projs = _select_projects(args)
    soft = config.get("thresholds.context_soft")
    now = timeutil.now()
    data = []
    for proj in projs:
        recs = [r for r in sessions.list_sessions(proj["slug"])
                if sessions.is_open(r)]
        data.append((proj, recs))
    if args.json:
        dump_json({"projects": [dict({"slug": p["slug"], "name": p.get("name"), "path": p.get("path"),
                                      "sessions": recs}, **({"trust": _trust_drift(p["slug"])}
                                                            if _trust_drift(p["slug"]) else {}))
                                for p, recs in data]})
        return 0
    if not data:
        print("no projects initialised (run `foreman init` in a project folder)")
        return 0
    for proj, recs in data:
        print("# %s (%s)" % (proj["slug"], proj.get("path")))
        for msg in _trust_drift(proj["slug"]):
            print("  trust: " + msg)
        if not recs:
            print("  (no sessions)")
            continue
        rows = []
        for r in recs:
            agent = r.get("agent") or "?"
            if r.get("model"):
                agent += "/" + r["model"]
            wt = r.get("worktree") or {}
            rows.append((r.get("title") or r["sid"], r["sid"], agent, _state_text(r, now),
                         "ctx " + format_context(r.get("context_tokens"), soft), r.get("owner") or "?",
                         ("%s @ %s" % (wt["branch"], wt["path"])) if wt else ""))
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for row in rows:
            print("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
    return 0
