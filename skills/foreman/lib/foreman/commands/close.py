"""foreman close: close a finished session and its terminal tab (spec §4.2, architecture §3)."""
from foreman import messaging, projects, sessions, transports, worktrees
from foreman.cliutil import CliError, add_project_arg, dump_json, find_session
from foreman.transports._proc import TransportError
from foreman.transports.base import TerminalGone


def register(subparsers):
    p = subparsers.add_parser("close", help="close a session")
    add_project_arg(p)
    p.add_argument("target", help="session id or title")
    p.add_argument("--user-consent", action="store_true", help="confirm closing a session owned by the user")
    p.add_argument("--json", action="store_true")


def run(args):
    rec = find_session(args.target, args)
    sid = rec["sid"]
    name = "%s (%s)" % (rec.get("title") or sid, sid)
    if not sessions.is_open(rec):
        wt_line = worktrees.release(rec)  # the agent may have ended on its own, or the work was cleaned up since
        if args.json:
            dump_json(dict(rec, worktree_result=wt_line))
        else:
            print("%s already closed" % sid)
            if wt_line:
                print(wt_line)
        return 0
    if rec.get("state") == "busy":
        raise CliError("cannot close %s: session is busy" % name, code=6)
    if rec.get("owner") == "user" and not args.user_consent:
        raise CliError("cannot close %s: session is owned by the user (pass --user-consent)" % name, code=7)
    if sessions.inbox_pending(sid):
        raise CliError("cannot close %s: pending messages in inbox" % name, code=8)

    tr = rec.get("transport") or {}
    if tr.get("kind") and tr.get("handle"):
        try:
            transport = transports.get_transport(tr["kind"])
            transport.close(tr["handle"])
        except NotImplementedError as exc:
            raise CliError(str(exc), code=9)
        except TerminalGone:
            pass
        except ValueError as exc:
            raise CliError(str(exc), code=2)
        except TransportError as exc:
            raise CliError(str(exc), code=1)

    now = sessions.set_lifecycle(sid, "closed")
    wt_line = worktrees.release(rec)
    slug = rec.get("project")
    if slug:
        proj = projects.load_project(slug)
        if proj and proj.get("orchestrator") == sid:
            proj["orchestrator"] = None
            projects.save_project(proj)
        projects.journal_append(
            slug, messaging.current_sender(), "close",
            "closed %s (%s)" % (rec.get("title") or sid, sid),
            data={"sid": sid, "title": rec.get("title"), "worktree": wt_line},
        )
    if args.json:
        dump_json(dict(now, worktree_result=wt_line))
    else:
        print("closed %s (%s)" % (sid, rec.get("title") or sid))
        if wt_line:
            print(wt_line)
    return 0
