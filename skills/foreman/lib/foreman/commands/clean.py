"""foreman clean: clean up idle work folders (spec §4.9)."""
from foreman import config, janitor


def register(subparsers):
    parser = subparsers.add_parser("clean", help="List and clean idle work folders")
    parser.add_argument("--dry-run", action="store_true", help="List idle folders without deleting")


def run(args):
    ttl_days = config.get("thresholds.work_ttl_days", 14)
    folders = janitor.scan_work_folders()
    candidates = [f for f in folders if f["idle_days"] >= ttl_days]

    for c in candidates:
        print("%s (idle %d days)" % (c["path"], int(c["idle_days"])))

    if not getattr(args, "dry_run", False):
        for c in candidates:
            janitor.safe_delete_dir(c["path"])

    return 0
