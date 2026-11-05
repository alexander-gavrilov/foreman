"""Talking to sessions and to the user (spec 4.7, architecture 7)."""
import os
import time
import types

from foreman import delivery, ids, paths, projects, sessions, store, timeutil, wake
from foreman.cliutil import CliError, find_session
from foreman.transports.base import TerminalGone

HANDLE_ENV = ("ORCA_TERMINAL_HANDLE", "HERDR_PANE_ID", "FOREMAN_TERMINAL_HANDLE")
AGENT_SESSION_ENV = ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "ANTIGRAVITY_CONVERSATION_ID")
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
    # a session reached without a terminal handle (claude-msg) still exports its own agent session id
    known = [env[k] for k in AGENT_SESSION_ENV if env.get(k)]
    if known:
        found = [r for r in sessions.list_sessions() if sessions.is_open(r) and
                 (r.get("agent_session_id") in known or set(known) & set(r.get("past_agent_session_ids") or []))]
        if found:
            return found[-1]
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
    """Resolve a session reference like every other command: titles are scoped to the caller's project."""
    return find_session(ref, types.SimpleNamespace(project=project))


def _require_agent_terminal(rec):
    """send/ask/compact reach only sessions whose terminal hosts an agent, never a plain shell."""
    status, _ = delivery.inspect(rec)
    if status == "shell":
        raise CliError("%s (%s) is not an agent terminal (its transport reports no agent there); "
                       "foreman will not type into a shell" % (rec.get("title") or rec["sid"], rec["sid"]))


def send_message(rec, text, sender=None, kind="message", msg_id=None, extra=None):
    """Queue `text` for session `rec` and deliver it now if the session is idle.
    Returns (status, message) with status delivered | queued | terminal_gone."""
    _require_agent_terminal(rec)
    sender = sender or current_sender()
    msg = sessions.inbox_enqueue(rec["sid"], text, sender=sender, kind=kind, msg_id=msg_id, extra=extra)
    return delivery.deliver_pending(rec["sid"]), msg


MAX_WAIT_S = 100


def ask(rec, text, sender=None):
    """Send a question; the answer (`foreman reply`) arrives later in the asker's inbox. Asynchronous:
    returns (status, msg_id) with status delivered | queued | terminal_gone."""
    msg_id = ids.new_msg_id()
    body = "%s\nReply with: foreman reply %s <answer>" % (text, msg_id)
    asker = current_session()
    extra = {"asker": asker["sid"]} if asker and asker["sid"] != rec["sid"] else None
    status, _ = send_message(rec, body, sender=sender, kind="ask", msg_id=msg_id, extra=extra)
    return status, msg_id


def wait_for_reply(msg_id, timeout, poll=0.2):
    """Block up to `timeout` seconds for the answer to `msg_id`. Returns the text or None. An answer that
    was already queued into the asker's inbox is marked delivered, so it is not delivered a second time."""
    deadline = time.monotonic() + timeout
    while True:
        answer = store.read_json(paths.reply_file(msg_id))
        if isinstance(answer, dict):
            asker = answer.get("asker")
            if asker and os.path.exists(paths.inbox_file(asker)):
                sessions.inbox_mark_delivered(asker, [reply_msg_id(msg_id)])
            return answer.get("text", "")
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def reply_msg_id(ask_id):
    return "re-" + ask_id


def _find_ask(msg_id):
    """-> (sid of the asked session, the ask message) or (None, None)."""
    for rec in sessions.list_sessions():
        for m in sessions.inbox_all(rec["sid"]):
            if m.get("id") == msg_id and m.get("kind") == "ask":
                return rec["sid"], m
    return None, None


def reply(msg_id, text, sender=None):
    """Answer an ask: queued into the asker's inbox (delivered at its next idle point, or at once if it is
    idle now) and recorded in replies/<msgid>.json for `ask --wait`. Only the asked session (or a person at
    the CLI, who has no session) may answer, and only once. CliError otherwise."""
    try:
        path = paths.reply_file(msg_id)
    except ValueError:
        raise CliError("unknown message %r" % msg_id)
    target, question = _find_ask(msg_id)
    if question is None:
        raise CliError("unknown message %r (no pending question with this id)" % msg_id)
    me = current_session()
    if me is not None and me["sid"] != target:
        raise CliError("question %s was asked of another session, not of you" % msg_id)
    if os.path.exists(path):
        raise CliError("message %s is already answered" % msg_id)
    sender = sender or current_sender()
    asker = question.get("asker")
    asker_rec = sessions.load_session(asker) if asker else None
    queue = asker_rec is not None and sessions.is_open(asker_rec)
    if queue:
        quoted = " ".join(str(question.get("text", "")).split("\nReply with:")[0].split())[:80]
        # keyed: of several concurrent repliers only one gets its row into the inbox
        row = sessions.inbox_enqueue(asker, "answer to your question %s («%s»): %s" % (msg_id, quoted, text),
                                     sender=sender, kind="reply", msg_id=reply_msg_id(msg_id),
                                     key="reply:" + msg_id)
        if row is None:
            raise CliError("message %s is already answered" % msg_id)
    try:
        store.write_json_exclusive(path, {"id": msg_id, "from": sender, "text": text, "asker": asker,
                                          "ts": timeutil.now_iso()})
    except FileExistsError:
        if queue:  # a stray row of a lost race must not be delivered
            sessions.inbox_mark_delivered(asker, [reply_msg_id(msg_id)])
        raise CliError("message %s is already answered" % msg_id)
    if queue:
        try:
            delivery.deliver_pending(asker)
        except Exception:
            pass


COMPACT_UNSUPPORTED = {"antigravity": "agy has no /compact command; rotate the session instead "
                                      "(`foreman rotate <sid>`)"}


def compact(rec, what=None):
    """Type the bare `/compact [what to keep]` into an idle agent terminal (no message framing: a framed
    message cannot carry a slash command). Never interrupts a busy session. -> "delivered"."""
    if rec.get("agent") in COMPACT_UNSUPPORTED:
        raise CliError("%s: %s" % (rec.get("title") or rec["sid"], COMPACT_UNSUPPORTED[rec["agent"]]))
    status, transport = delivery.inspect(rec)
    name = "%s (%s)" % (rec.get("title") or rec["sid"], rec["sid"])
    if status == "shell":
        _require_agent_terminal(rec)
    if status == "none":
        raise CliError("%s has no terminal foreman can type into" % name, code=1)
    if status == "gone":
        raise CliError("terminal gone: %s" % name, code=3)
    if status != "idle":
        raise CliError("%s is busy; compact it at its next idle point (foreman never interrupts)" % name, code=1)
    line = "/compact" + (" " + " ".join(what.split()) if what and what.strip() else "")
    try:
        transport.send(rec["transport"]["handle"], line)
    except TerminalGone:
        sessions.set_state(rec["sid"], "gone")
        raise CliError("terminal gone: %s" % name, code=3)
    return "delivered"


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
