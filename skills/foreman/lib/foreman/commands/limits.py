"""foreman limits: subscription windows with usage, reset time and reading age."""
from foreman import cliutil, config, limits, paths, store, timeutil


def register(subparsers):
    parser = subparsers.add_parser("limits", help="Show subscription limits, windows and reset times")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--line", action="store_true", help="One-line summary for status reports")


def run(args):
    if args.line:
        print(limits.summary_line())
        return 0

    all_levels = limits.levels()
    limit_warn = config.get("thresholds.limit_warn", 70)
    limit_stop = config.get("thresholds.limit_stop", 90)

    providers = ["claude", "antigravity"]
    import os
    if os.path.exists(paths.limits_file("codex")):
        providers.append("codex")

    data = {}
    for prov in providers:
        windows = limits.load_provider_limits(prov)
        prov_level = all_levels.get(prov, "unknown")
        prov_windows = {}
        for w_name, w_data in windows.items():
            if not isinstance(w_data, dict):
                continue
            used = w_data.get("used_pct")
            level = limits._window_level(used, limit_warn, limit_stop)
            obs = w_data.get("observed_at")
            prov_windows[w_name] = {
                "used_pct": used,
                "resets_at": w_data.get("resets_at"),
                "observed_at": obs,
                "age": timeutil.age_text(obs) if obs else "?",
                "level": level,
            }
        data[prov] = {
            "level": prov_level,
            "windows": prov_windows,
        }

    if args.json:
        cliutil.dump_json(data)
        return 0

    for prov in providers:
        prov_info = data[prov]
        print("%s (%s):" % (prov.capitalize(), prov_info["level"]))
        if not prov_info["windows"]:
            print("  (no readings)")
        else:
            # Sort weekly/seven_day first, then five_hour, then others
            def sort_key(k):
                if "week" in k or "seven" in k:
                    return (0, k)
                if "five" in k or "5h" in k:
                    return (1, k)
                return (2, k)

            for w_name in sorted(prov_info["windows"].keys(), key=sort_key):
                w = prov_info["windows"][w_name]
                used_str = "%d%%" % w["used_pct"] if w["used_pct"] is not None else "?"
                resets_str = w["resets_at"] or "-"
                print("  %-14s %5s  resets %-20s  age %-5s  [%s]" % (
                    w_name + ":", used_str, resets_str, w["age"], w["level"]))

    return 0
