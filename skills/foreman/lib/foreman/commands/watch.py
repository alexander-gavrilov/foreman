"""foreman watch: run the event check, loop, or list active watches (spec §4.6)."""
import time

from foreman import cliutil, config, limits, paths, sessions, store, timeutil, watch


def register(subparsers):
    parser = subparsers.add_parser("watch", help="Watch loop pass and active watches")
    sub = parser.add_subparsers(dest="watch_subcommand", metavar="<subcommand>")

    p_run = sub.add_parser("run", help="Run one watch pass")

    p_loop = sub.add_parser("loop", help="Run the watch loop continuously")
    p_loop.add_argument("--interval", type=int, default=60, help="Interval in seconds (default: 60)")

    p_list = sub.add_parser("list", help="List active watches and loop status")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")


def _last_hook_events():
    """Return {agent: latest_iso_timestamp or None} across events.jsonl and all sessions."""
    latest = {"claude": None, "antigravity": None}
    for s in sessions.list_sessions():
        agent = s.get("agent")
        if agent == "agy":
            agent = "antigravity"
        ev_at = s.get("last_event_at")
        if agent in latest and ev_at:
            if latest[agent] is None or ev_at > latest[agent]:
                latest[agent] = ev_at

    for ev in store.read_jsonl(paths.events_file()):
        agent = ev.get("agent")
        if agent == "agy":
            agent = "antigravity"
        ts = ev.get("ts")
        if agent in latest and ts:
            if latest[agent] is None or ts > latest[agent]:
                latest[agent] = ts

    return latest


def run(args):
    subcmd = getattr(args, "watch_subcommand", None)
    if not subcmd or subcmd == "run":
        summary = watch.run_pass()
        print("watch pass complete: %d wakes, %d deliveries" % (summary.get("wakes", 0), summary.get("deliveries", 0)))
        return 0

    if subcmd == "loop":
        interval = args.interval or 60
        try:
            while True:
                watch.run_pass(interval_s=interval)
                time.sleep(interval)
        except KeyboardInterrupt:
            return 130

    if subcmd == "list":
        hb = store.read_json(paths.watch_heartbeat_file(), default=None)
        loop_running = False
        loop_info = "NOT RUNNING"
        if isinstance(hb, dict):
            last_run = hb.get("last_run_at")
            interval = hb.get("interval_s", 60)
            pid = hb.get("pid")
            age = timeutil.age_seconds(last_run)
            if age is not None and age <= 3 * interval:
                loop_running = True
                loop_info = "RUNNING (pid %s, interval %ds, last run %s ago)" % (pid, interval, timeutil.humanize(age))
            else:
                age_str = ("%s ago" % timeutil.humanize(age)) if age is not None else "never"
                loop_info = "NOT RUNNING (last run %s)" % age_str

        non_closed = [
            s for s in sessions.list_sessions()
            if s.get("lifecycle") != "closed" and s.get("state") != "closed"
        ]
        limit_warn = config.get("thresholds.limit_warn", 70)
        limit_stop = config.get("thresholds.limit_stop", 90)
        prov_levels = limits.levels()
        hook_events = _last_hook_events()

        if getattr(args, "json", False):
            cliutil.dump_json({
                "loop": {
                    "running": loop_running,
                    "heartbeat": hb,
                },
                "sessions": non_closed,
                "limits": {
                    "warn_threshold": limit_warn,
                    "stop_threshold": limit_stop,
                    "levels": prov_levels,
                },
                "hook_events": {
                    a: {
                        "last_event_at": ts,
                        "age": timeutil.age_text(ts) if ts else "never",
                    }
                    for a, ts in hook_events.items()
                },
            })
            return 0

        print("Loop: %s" % loop_info)
        print("Watched sessions (%d):" % len(non_closed))
        if not non_closed:
            print("  (none)")
        else:
            for s in non_closed:
                title = s.get("title") or s["sid"]
                print("  %s (title: %s, state: %s)" % (s["sid"], title, s.get("state")))

        hook_parts = []
        for a in ("claude", "antigravity"):
            ts = hook_events.get(a)
            age = timeutil.age_text(ts) if ts else "never"
            hook_parts.append("%s: %s" % (a.capitalize(), age if age == "never" else ("%s ago" % age)))
        print("Hook events: %s" % ", ".join(hook_parts))

        print("Limits: warn >= %d%%, stop >= %d%%" % (limit_warn, limit_stop))
        for prov in ("claude", "antigravity"):
            lvl = prov_levels.get(prov, "unknown")
            p_lim = limits.load_provider_limits(prov)
            readings = [
                "%s: %d%%" % (k, w["used_pct"])
                for k, w in p_lim.items()
                if isinstance(w, dict) and w.get("used_pct") is not None
            ]
            readings_str = ", ".join(readings) if readings else "no readings"
            print("  %s: %s (%s)" % (prov.capitalize(), lvl, readings_str))

        return 0

    return 2
