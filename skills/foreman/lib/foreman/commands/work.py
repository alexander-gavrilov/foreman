"""foreman work: manage work artifact folders outside the repo (spec §4.9)."""
import os
import re

from foreman import cliutil, janitor, paths, projects, timeutil


def register(subparsers):
    parser = subparsers.add_parser("work", help="Create or close work artifact folders")
    sub = parser.add_subparsers(dest="work_subcommand", metavar="<subcommand>")

    p_new = sub.add_parser("new", help="Create a fresh work folder")
    p_new.add_argument("topic", help="Topic or brief name for the work")
    cliutil.add_project_arg(p_new)

    p_close = sub.add_parser("close", help="Close a work folder and delete it")
    p_close.add_argument("dir", help="Path to work folder")
    p_close.add_argument("--promote", metavar="SUMMARY", help="Summary to append to board backlog")


def run(args):
    subcmd = getattr(args, "work_subcommand", None)
    if not subcmd:
        raise cliutil.CliError("missing work subcommand (try `foreman work new` or `foreman work close`)")

    if subcmd == "new":
        proj = cliutil.resolve_project(args)
        slug = proj["slug"]
        today = timeutil.now().strftime("%Y-%m-%d")
        clean_topic = re.sub(r"[^a-zA-Z0-9_-]+", "-", args.topic).strip("-")
        folder_name = "%s-%s" % (today, clean_topic)
        work_dir = os.path.join(paths.agent_work_dir(slug), folder_name)
        os.makedirs(work_dir, exist_ok=True)
        print(work_dir)
        return 0

    if subcmd == "close":
        target = os.path.realpath(args.dir)
        root = os.path.realpath(paths.agent_work_root())
        if not target.startswith(root + os.sep):
            raise cliutil.CliError("refusing to close directory outside agent-work: %s" % args.dir)
        if not os.path.isdir(target):
            raise cliutil.CliError("work directory not found: %s" % args.dir)

        rel = os.path.relpath(target, root)
        parts = rel.split(os.sep)
        slug = parts[0]

        if getattr(args, "promote", None):
            projects.append_backlog(slug, args.promote)

        janitor.safe_delete_dir(target)
        return 0

    return 2
