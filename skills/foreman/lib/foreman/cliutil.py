"""Helpers shared by command modules."""
import json
import os
import sys

from foreman import projects


class CliError(Exception):
    """A user-facing error: printed as `foreman: <message>` to stderr, process exits with `code`."""

    def __init__(self, message, code=2):
        super().__init__(message)
        self.message = message
        self.code = code


def add_project_arg(parser):
    parser.add_argument("--project", metavar="SLUG", help="project slug (default: the project containing the cwd)")


def resolve_project(args, cwd=None):
    """--project <slug>, else the initialised project containing the cwd; CliError otherwise."""
    slug = getattr(args, "project", None)
    if slug:
        proj = projects.load_project(slug)
        if not proj:
            raise CliError("unknown project %r (known: %s)" % (
                slug, ", ".join(p["slug"] for p in projects.list_projects()) or "none"))
        return proj
    proj = projects.project_for_path(cwd or os.getcwd())
    if not proj:
        raise CliError("no foreman project here: %s is not inside an initialised project "
                       "(run `foreman init` or pass --project <slug>)" % (cwd or os.getcwd()))
    return proj


def dump_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def read_text_arg(value):
    """'-' means: read the text from stdin."""
    return sys.stdin.read().rstrip("\n") if value == "-" else value
