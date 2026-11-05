"""Helpers shared by command modules."""
import json
import os
import sys

from foreman import projects, sessions
from foreman.errors import ForemanError


class CliError(ForemanError):
    """A user-facing error: printed as `foreman: <message>` to stderr, process exits with `code`."""

    def __init__(self, message, code=2):
        super().__init__(message, code)


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


def caller_project(args, cwd=None):
    """The --project slug if given, else the slug of the project containing the cwd, else None (no error)."""
    slug = getattr(args, "project", None)
    if slug:
        return slug
    proj = projects.project_for_path(cwd or os.getcwd())
    return proj["slug"] if proj else None


def find_session(ref, args, cwd=None):
    """Resolve a session reference for a command: titles are scoped to the caller's project; an ambiguous
    title across projects, or no match at all, is a CliError."""
    try:
        rec = sessions.find(ref, project=caller_project(args, cwd))
    except sessions.AmbiguousSession as exc:
        raise CliError(exc.message, exc.code)
    if rec is None:
        raise CliError("no session %r" % ref)
    return rec


def dump_json(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def read_text_arg(value):
    """'-' means: read the text from stdin."""
    return sys.stdin.read().rstrip("\n") if value == "-" else value
