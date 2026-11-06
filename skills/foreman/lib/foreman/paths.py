"""All filesystem locations (architecture §2). Every function reads the environment at call time,
so tests can point FOREMAN_HOME / HOME at temporary directories."""
import contextlib
import os
import threading

_pinned = threading.local()


@contextlib.contextmanager
def pinned_home(path):
    """Resolve home() to `path` on this thread: a background thread keeps the state root it was started for even
    if the process environment changes meanwhile."""
    before = getattr(_pinned, "path", None)
    _pinned.path = path
    try:
        yield
    finally:
        _pinned.path = before


def default_home():
    state = os.environ.get("XDG_STATE_HOME") or os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(state, "foreman")


def home():
    """$FOREMAN_HOME, else ${XDG_STATE_HOME:-~/.local/state}/foreman."""
    pinned = getattr(_pinned, "path", None)
    if pinned:
        return pinned
    explicit = os.environ.get("FOREMAN_HOME")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    return default_home()


def _name(value):
    """Validate a single path component (slug, sid, id) so ids can never escape the state root."""
    if not isinstance(value, str) or not value or value in (".", "..") or "/" in value or "\\" in value \
            or "\0" in value:
        raise ValueError("invalid name: %r" % (value,))
    return value


def _join(*parts):
    return os.path.join(home(), *parts)


def config_file():
    return _join("config.json")


def secrets_file():
    return _join("secrets.json")


def projects_dir():
    return _join("projects")


def project_dir(slug):
    return _join("projects", _name(slug))


def project_file(slug):
    return os.path.join(project_dir(slug), "project.json")


def board_file(slug):
    return os.path.join(project_dir(slug), "board.json")


def journal_file(slug):
    return os.path.join(project_dir(slug), "journal.jsonl")


def chat_file(slug):
    return os.path.join(project_dir(slug), "chat.jsonl")


def lessons_file(slug):
    return os.path.join(project_dir(slug), "lessons.md")


def sessions_dir():
    return _join("sessions")


def pending_orchestrator_file(slug):
    return _join("sessions", "pending", _name(slug) + ".json")


def session_file(sid):
    return _join("sessions", _name(sid) + ".json")


def inbox_dir():
    return _join("inbox")


def inbox_file(sid):
    return _join("inbox", _name(sid) + ".jsonl")


def limits_dir():
    return _join("limits")


def limits_file(provider):
    return _join("limits", _name(provider) + ".json")


def events_file():
    return _join("events.jsonl")


def watch_dir():
    return _join("watch")


def watch_state_file():
    return _join("watch", "state.json")


def watch_heartbeat_file():
    return _join("watch", "heartbeat.json")


def findings_dir():
    return _join("findings")


def finding_file(fid):
    return _join("findings", _name(fid) + ".json")


def agent_work_root():
    return _join("agent-work")


def agent_work_dir(slug):
    return _join("agent-work", _name(slug))


def worktrees_root():
    return _join("worktrees")


def worktrees_dir(slug):
    return _join("worktrees", _name(slug))


def replies_dir():
    return _join("replies")


def reply_file(msgid):
    return _join("replies", _name(msgid) + ".json")
