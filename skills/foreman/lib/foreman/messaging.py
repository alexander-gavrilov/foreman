"""Talking to sessions and to the user (spec 4.7, architecture 7)."""
import os
import time

from foreman import delivery, ids, paths, projects, sessions, store, timeutil, wake
from foreman.cliutil import CliError

HANDLE_ENV = ("ORCA_TERMINAL_HANDLE", "HERDR_PANE_ID", "FOREMAN_TERMINAL_HANDLE")
GONE_TEXT = "терминал оркестратора закрыт"


def current_session(env=None):
    """The session this process runs in: $FOREMAN_SESSION (a sid), else the terminal handle from the
    environment. None when unknown."""
    env = os.environ if env is None else env
    if env.get("FOREMAN_SESSION"):
        rec = sessions.load_session(env["FOREMAN_SESSION"])
        if rec:
            return rec
    for key in HANDLE_ENV:
        if env.get(key):
            rec = sessions.find_by_handle(env[key])
            if rec:
                return rec
    return None


def current_sender(env=None):
    env = os.environ if env is None else env
    if env.get("FOREMAN_ACTOR"):
        return env["FOREMAN_ACTOR"]
    rec = current_session(env)
    return (rec.get("title") or rec["sid"]) if rec else "cli"


def resolve_project_slug(explicit=None, cwd=None):
    """--project, else the current session's project, else the initialised project containing the cwd."""
    if explicit:
        if not projects.load_project(explicit):
            raise CliError("unknown project %r" % explicit)
        return explicit
    rec = current_session()
    if rec and rec.get("project"):
        return rec["project"]
    proj = projects.project_for_path(cwd or os.getcwd())
    if not proj:
        raise CliError("no foreman project here (run inside an initialised project or pass --project <slug>)")
    return proj["slug"]


def find_target(ref, project=None):
    rec = sessions.find(ref, project) if project else None
    rec = rec or sessions.find(ref)
    if rec is None:
        raise CliError("unknown session %r" % ref)
    return rec


def send_message(rec, text, sender=None, kind="message", msg_id=None):
    """Queue `text` for session `rec` and deliver it now if the session is idle.
    Returns (status, message) with status delivered | queued | terminal_gone."""
    sender = sender or current_sender()
    msg = sessions.inbox_enqueue(rec["sid"], text, sender=sender, kind=kind, msg_id=msg_id)
    return delivery.deliver_pending(rec["sid"]), msg


def ask(rec, text, sender=None, timeout=900.0, poll=0.2):
    """Send a question and wait for `foreman reply`. Returns ("reply", text) | ("timeout", None) |
    ("terminal_gone", None)."""
    msg_id = ids.new_msg_id()
    body = "%s\nReply with: foreman reply %s <answer>" % (text, msg_id)
    status, _ = send_message(rec, body, sender=sender, kind="ask", msg_id=msg_id)
    if status == "terminal_gone":
        return "terminal_gone", None
    deadline = time.monotonic() + timeout
    while True:
        answer = store.read_json(paths.reply_file(msg_id))
        if isinstance(answer, dict):
            return "reply", answer.get("text", "")
        if time.monotonic() >= deadline:
            return "timeout", None
        time.sleep(poll)


def _ask_exists(msg_id):
    for rec in sessions.list_sessions():
        for m in sessions.inbox_all(rec["sid"]):
            if m.get("id") == msg_id and m.get("kind") == "ask":
                return True
    return False


def reply(msg_id, text, sender=None):
    """Answer an ask. CliError for an unknown or already answered message id."""
    try:
        path = paths.reply_file(msg_id)
    except ValueError:
        raise CliError("unknown message %r" % msg_id)
    if os.path.exists(path):
        raise CliError("message %s is already answered" % msg_id)
    if not _ask_exists(msg_id):
        raise CliError("unknown message %r (no pending question with this id)" % msg_id)
    store.write_json(path, {"id": msg_id, "from": sender or current_sender(), "text": text,
                            "ts": timeutil.now_iso()})


def say(project, text, question=False, options=None, notify_user=False):
    """Orchestrator -> user: append to the project chat; fan out to the notifiers for a question or on request.
    A failing notifier never fails the call."""
    msg = projects.chat_append(project, "orchestrator", text, channel="cli", question=question,
                               options=options)
    if question or notify_user:
        try:
            from foreman import notify
            notify.notify(project, text, msg_id=msg["id"], question=bool(question), options=list(options or []))
        except Exception:
            pass
    return msg


def _mark_answered(project, question_id, answer_id):
    def fn(rows):
        for row in rows:
            if row.get("id") == question_id and row.get("question") and not row.get("answered_by"):
                row["answered_by"] = answer_id
        return rows
    path = paths.chat_file(project)
    if os.path.exists(path):
        store.rewrite_jsonl(path, fn)


def user_reply(project, text, reply_to=None, channel="web"):
    """A message from the user (web, Telegram, CLI): appended to the chat and queued for the orchestrator.
    If its terminal is gone, a foreman message in the chat says so. -> {"id", "delivery"}"""
    msg = projects.chat_append(project, "user", text, channel=channel, reply_to=reply_to)
    context = ""
    if reply_to:
        _mark_answered(project, reply_to, msg["id"])
        quoted = next((r for r in projects.chat_list(project) if r.get("id") == reply_to), None)
        if quoted:
            context = "(reply to «%s» [%s]) " % (" ".join(str(quoted.get("text", "")).split())[:120], reply_to)
    orch = wake.find_orchestrator(project)
    status = "terminal_gone"
    if orch is not None:
        sessions.inbox_enqueue(orch["sid"], context + text, sender="user", kind="user")
        status = delivery.deliver_pending(orch["sid"])
    if status == "terminal_gone":
        projects.chat_append(project, "foreman", GONE_TEXT, channel="system")
    return {"id": msg["id"], "delivery": status}


def stage(task, stage_name, done=False, project=None):
    """A worker's one-line stage report to its orchestrator (the orchestrator alone writes the board)."""
    slug = resolve_project_slug(project)
    line = "stage %s → %s%s" % (task, stage_name, " (done)" if done else "")
    msg = wake.queue_for_orchestrator(slug, line, sender=current_sender(), kind="stage")
    if msg is None and wake.find_orchestrator(slug) is None:
        raise CliError("no orchestrator is running for project %s" % slug, code=1)
    return msg
