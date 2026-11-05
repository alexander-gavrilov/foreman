"""foreman rotate: freeze a session and open its replacement, requesting a direct-message handoff (spec §4.2, arch §7a)."""
import re
import sys

from foreman import delivery, messaging, projects, sessions, transports
from foreman.cliutil import CliError, add_project_arg, dump_json, find_session
from foreman.commands import open as open_cmd
from foreman.commands import orchestrator
from foreman.transports._proc import TransportError


def _next_title(old_title, explicit_title=None):
    if explicit_title:
        return explicit_title
    m = re.match(r"^(.*)-r(\d+)$", old_title or "")
    if m:
        return "%s-r%d" % (m.group(1), int(m.group(2)) + 1)
    return "%s-r2" % (old_title or "session")


def _rotation_prompt(old, proj):
    old_sid = old["sid"]
    old_title = old.get("title") or old_sid
    purpose = old.get("purpose") or ""
    tx = old.get("transcript_path")
    tx_note = " (%s)" % tx if tx else ""
    body = (
        "You replace session %s (%s).\n"
        "Purpose: %s\n"
        "Wait for the handoff message from %s before starting work.\n"
        "If you have questions for %s, batch ALL of them into ONE `foreman ask %s \"...\"` within ~1 hour "
        "while its prompt cache is warm.\n"
        "Afterwards, follow the order of cost: first re-read the handoff, then search the old transcript%s, "
        "and only last ask %s why-questions (batched)."
        % (old_sid, old_title, purpose, old_sid, old_sid, old_sid, tx_note, old_sid)
    )
    if old.get("role") == "orchestrator":
        return orchestrator.render_briefing(proj) + "\n\n" + body
    return body


def _update_board_owners(slug, old, new_rec):
    board = projects.load_board(slug)
    changed = False
    old_sid = old["sid"]
    old_title = old.get("title")
    for task in board.get("tasks") or []:
        owner = task.get("owner")
        if not owner or task.get("status") == "done":
            continue
        if owner == old_sid:
            task["owner"] = "%s → %s" % (old_sid, new_rec["sid"])
            changed = True
        elif old_title and owner == old_title:
            task["owner"] = "%s → %s" % (old_title, new_rec["title"])
            changed = True
        elif owner.endswith(" → " + old_sid):
            task["owner"] = "%s → %s" % (owner, new_rec["sid"])
            changed = True
        elif old_title and owner.endswith(" → " + old_title):
            task["owner"] = "%s → %s" % (owner, new_rec["title"])
            changed = True
    if changed:
        projects.save_board(slug, board)


def register(subparsers):
    p = subparsers.add_parser("rotate", help="freeze a session and replace it with a fresh one")
    add_project_arg(p)
    p.add_argument("target", help="session id or title")
    p.add_argument("--agent", help="override agent for the replacement session")
    p.add_argument("--model", help="override model for the replacement session")
    p.add_argument("--effort", help="override reasoning effort")
    p.add_argument("--title", help="override title (default: <old>-r2, <old>-r3, ...)")
    p.add_argument("--transport", help="orca | herdr | fake")
    p.add_argument("--json", action="store_true")


def run(args):
    old = find_session(args.target, args)
    old_sid = old["sid"]
    name = "%s (%s)" % (old.get("title") or old_sid, old_sid)
    if old.get("lifecycle") in ("rotated", "closed") or old.get("state") in ("rotated", "closed", "gone"):
        why = old.get("lifecycle") if old.get("lifecycle") in ("rotated", "closed") else old.get("state")
        raise CliError("cannot rotate %s: session is already %s" % (name, why), code=2)
    if old.get("state") == "busy":
        raise CliError("cannot rotate %s: session is busy" % name, code=6)
    if sessions.inbox_pending(old_sid):
        raise CliError("cannot rotate %s: session has undelivered inbox messages" % name, code=8)

    tr = old.get("transport") or {}
    if tr.get("kind") and tr.get("handle"):
        try:
            old_transport = transports.get_transport(tr["kind"])
            if not old_transport.alive(tr["handle"]):
                sessions.set_state(old_sid, "gone")
                raise CliError("terminal gone: %s" % name, code=3)
        except ValueError as exc:
            raise CliError(str(exc), code=2)
        except TransportError as exc:
            raise CliError(str(exc), code=1)

    slug = old["project"]
    proj = projects.load_project(slug) or {"slug": slug, "name": slug, "path": old.get("cwd") or ""}
    agent = args.agent or old.get("agent") or "claude"
    if args.model is not None:
        model = args.model
    elif args.agent and args.agent != old.get("agent"):
        model = None
    else:
        model = old.get("model")
    effort = args.effort if args.effort is not None else old.get("effort")
    new_title = _next_title(old.get("title") or old_sid, args.title)
    prompt = _rotation_prompt(old, proj)
    tkind = args.transport or tr.get("kind")

    new_rec, note = open_cmd.open_session(
        project=dict(proj, path=old.get("cwd") or proj.get("path") or ""),
        purpose=old.get("purpose"), prompt=prompt, title=new_title,
        kind="orchestrator" if old.get("role") == "orchestrator" else "routine",
        agent=agent, model=model, effort=effort,
        transport_kind=tkind, owner=old.get("owner") or "orchestrator",
        role=old.get("role") or "worker", replaces=old_sid, journal=False,
    )

    sessions.update_session(old_sid, replaced_by=new_rec["sid"])
    sessions.set_lifecycle(old_sid, "rotated")
    _update_board_owners(slug, old, new_rec)

    handoff_req = (
        "You have been rotated to %s (%s). Write your handoff (what is done, what remains, key files, "
        "open questions) and deliver it directly with `foreman send %s -`."
        % (new_rec["title"], new_rec["sid"], new_rec["sid"])
    )
    sessions.inbox_enqueue(old_sid, handoff_req, sender="foreman", kind="handoff")
    dstatus = delivery.deliver_pending(old_sid)
    if dstatus == "terminal_gone":
        sys.stderr.write("foreman: warning: terminal gone for %s before handoff request could be delivered\n"
                         % old_sid)

    am = (new_rec.get("agent") or "?") + ("/" + new_rec["model"] if new_rec.get("model") else "")
    line = "rotated %s → %s %s (%s)%s" % (
        old_sid, new_rec["sid"], new_rec["title"], am, " — " + note if note else ""
    )
    projects.journal_append(
        slug, messaging.current_sender(), "rotate", line,
        data={"old_sid": old_sid, "new_sid": new_rec["sid"], "title": new_rec["title"],
              "agent": new_rec.get("agent"), "model": new_rec.get("model"), "note": note},
    )
    if args.json:
        dump_json({"old": sessions.load_session(old_sid), "new": new_rec, "note": note, "handoff_delivery": dstatus})
    else:
        print("%s → %s" % (old_sid, open_cmd.format_open_line(new_rec, note)))
    return 0
