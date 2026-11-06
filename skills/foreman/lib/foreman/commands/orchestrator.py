"""foreman orchestrator: start, show, or adopt a project's orchestrator session (spec §4.1, architecture §3)."""
import os

from foreman import messaging, projects, routing, sessions, transports
from foreman.cliutil import CliError, add_project_arg, dump_json, resolve_project
from foreman.commands import open as open_cmd
from foreman.transports._proc import TransportError

TEMPLATE_FILE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, os.pardir,
                 "templates", "orchestrator-briefing.md")
)


def template_path():
    return TEMPLATE_FILE


def render_briefing(project):
    try:
        with open(template_path(), "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise CliError("cannot read orchestrator briefing template: %s" % exc, code=1)
    replacements = {
        "{{slug}}": str(project.get("slug") or ""),
        "{{name}}": str(project.get("name") or project.get("slug") or ""),
        "{{path}}": str(project.get("path") or ""),
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.rstrip("\n")


def _is_live_orch(rec):
    return (
        isinstance(rec, dict)
        and rec.get("role") == "orchestrator"
        and sessions.is_open(rec)
        and not sessions.is_stray(rec)
        and rec.get("lifecycle") != "rotated"
        and rec.get("state") != "gone"
    )


def find_live_orchestrator(slug):
    proj = projects.load_project(slug)
    ref = (proj or {}).get("orchestrator")
    if isinstance(ref, dict):
        ref = ref.get("sid")
    if isinstance(ref, str):
        rec = sessions.load_session(ref)
        if _is_live_orch(rec):
            return rec
    live = [r for r in sessions.list_sessions(slug) if _is_live_orch(r)]
    return live[-1] if live else None


def _find_transport_handle(handle, explicit_kind=None):
    if explicit_kind:
        try:
            tr = transports.get_transport(explicit_kind)
            for info in tr.list():
                if info.handle == handle:
                    return explicit_kind, tr, info
        except ValueError as exc:
            raise CliError(str(exc), code=2)
        except (TransportError, RuntimeError) as exc:
            raise CliError(str(exc), code=1)
        return None, None, None

    kinds = []
    if os.environ.get("FOREMAN_TRANSPORT"):
        kinds.append(os.environ["FOREMAN_TRANSPORT"])
    for k in ("orca", "herdr", "fake", "claude-msg"):
        if k not in kinds:
            kinds.append(k)
    for k in kinds:
        try:
            tr = transports.get_transport(k)
            for info in tr.list():
                if info.handle == handle:
                    return k, tr, info
        except Exception:
            continue
    return None, None, None


def register(subparsers):
    p = subparsers.add_parser("orchestrator", help="start, inspect, or adopt a project's orchestrator")
    sub = p.add_subparsers(dest="orch_cmd", metavar="<action>", required=True)

    st = sub.add_parser("start", help="open a new orchestrator session for a project")
    add_project_arg(st)
    st.add_argument("--agent", help="claude | antigravity (default: claude)")
    st.add_argument("--model", help="model name")
    st.add_argument("--effort", help="reasoning effort")
    st.add_argument("--title", help="tab and session title (default: <slug>-orch)")
    st.add_argument("--transport", help="orca | herdr | fake")
    st.add_argument("--json", action="store_true")

    sh = sub.add_parser("show", help="show the current orchestrator of a project")
    add_project_arg(sh)
    sh.add_argument("--json", action="store_true")

    ad = sub.add_parser("adopt", help="adopt an existing session or live terminal handle as the project orchestrator (user-only)")
    add_project_arg(ad)
    ad.add_argument("target", help="session id, title, or transport handle")
    ad.add_argument("--title", help="override session title")
    ad.add_argument("--transport", help="orca | herdr | fake")
    ad.add_argument("--json", action="store_true")


def _start(args):
    proj = resolve_project(args)
    slug = proj["slug"]
    existing = find_live_orchestrator(slug)
    if existing is not None:
        raise CliError("project %s already has a live orchestrator (%s)" % (slug, existing["sid"]), code=2)
    if args.agent:
        if args.agent not in open_cmd.KNOWN_AGENTS:
            raise CliError("unknown agent %r (known: %s)" % (args.agent, ", ".join(open_cmd.KNOWN_AGENTS)), code=2)
        if args.agent not in routing.ORCHESTRATOR_AGENTS:
            raise CliError("agent %r is worker-only in v1 and cannot be the orchestrator (use: %s)"
                           % (args.agent, ", ".join(routing.ORCHESTRATOR_AGENTS)), code=2)
    briefing = render_briefing(proj)
    title = args.title or ("%s-orch" % slug)
    rec, note = open_cmd.open_session(
        project=proj, purpose="orchestrator of %s" % slug, prompt=briefing, title=title,
        kind="orchestrator", agent=args.agent, model=args.model, effort=args.effort,
        transport_kind=args.transport, owner="user", role="orchestrator",
    )
    if args.json:
        dump_json(dict(rec, note=note))
    else:
        print(open_cmd.format_open_line(rec, note))
    return 0


def _show(args):
    proj = resolve_project(args)
    slug = proj["slug"]
    rec = find_live_orchestrator(slug)
    if args.json:
        dump_json({"project": slug, "orchestrator": rec})
        return 0
    if rec is None:
        print("%s: none" % slug)
        return 0
    am = (rec.get("agent") or "?") + ("/" + rec["model"] if rec.get("model") else "")
    print("%s %s (%s, %s)" % (rec["sid"], rec.get("title") or rec["sid"], am, rec.get("state")))
    return 0


def _adopt(args):
    rebind = False
    proj = resolve_project(args)
    slug = proj["slug"]
    try:
        rec = sessions.find(args.target, project=slug) or sessions.find(args.target)
    except sessions.AmbiguousSession as exc:
        raise CliError(exc.message, exc.code)

    existing = find_live_orchestrator(slug)
    if existing is not None and (rec is None or existing["sid"] != rec["sid"]):
        raise CliError("project %s already has a live orchestrator (%s)" % (slug, existing["sid"]), code=2)

    if rec is not None:
        if rec.get("project") and rec["project"] != slug:
            raise CliError("session %s belongs to project %s, not %s" % (rec["sid"], rec["project"], slug), code=2)
        if not sessions.is_open(rec) or rec.get("lifecycle") == "rotated" or rec.get("state") == "gone":
            raise CliError("cannot adopt %s: session is not active" % rec["sid"], code=2)
        tr = rec.get("transport") or {}
        tkind = args.transport or tr.get("kind")
        handle = tr.get("handle")
        if not tkind or not handle:
            raise CliError("session %s has no transport handle" % rec["sid"], code=2)
        _, _, info = _find_transport_handle(handle, explicit_kind=tkind)
        if info is None:
            raise CliError("terminal gone: %s (%s)" % (rec.get("title") or rec["sid"], rec["sid"]), code=3)
        if not info.agent:
            raise CliError(
                "terminal %s (%s) is a plain shell (transport reports no agent there); "
                "cannot adopt a shell as orchestrator" % (handle, rec["sid"]),
                code=2,
            )
        agent = info.agent or rec.get("agent")
        if agent not in routing.ORCHESTRATOR_AGENTS:
            raise CliError("agent %r is worker-only in v1 and cannot be the orchestrator (use: %s)"
                           % (agent, ", ".join(routing.ORCHESTRATOR_AGENTS)), code=2)
        upd = {"role": "orchestrator", "owner": "user", "agent": agent}
        if args.title:
            upd["title"] = args.title
        if rec.get("agent_session_id"):
            # D59: an agent restarted by hand in this terminal looks like a child session until the record forgets the
            # old id; the next hook from the terminal then binds to whatever session runs there now
            upd["agent_session_id"] = None
            upd["state"] = "idle" if info.idle is not False else "busy"
            rebind = True
        rec = sessions.update_session(rec["sid"], **upd)
    else:
        tkind, _, info = _find_transport_handle(args.target, explicit_kind=args.transport)
        if info is None:
            raise CliError("no session or live terminal %r" % args.target, code=2)
        if not info.agent:
            raise CliError(
                "terminal %s is a plain shell (transport %s reports no agent there); "
                "cannot adopt a shell as orchestrator" % (args.target, tkind),
                code=2,
            )
        if info.agent not in routing.ORCHESTRATOR_AGENTS:
            raise CliError("agent %r is worker-only in v1 and cannot be the orchestrator (use: %s)"
                           % (info.agent, ", ".join(routing.ORCHESTRATOR_AGENTS)), code=2)
        title = args.title or info.title or ("%s-orch" % slug)
        state = "idle" if info.idle is not False else "busy"
        rec = sessions.create_session(
            project=slug, agent=info.agent, model=None, effort=None,
            role="orchestrator", owner="user",
            purpose="orchestrator of %s" % slug, title=title,
            transport={"kind": tkind, "handle": info.handle},
            cwd=info.cwd or proj["path"], state=state, lifecycle="active",
        )

    proj = projects.load_project(slug)
    if proj:
        proj["orchestrator"] = rec["sid"]
        projects.save_project(proj)
    projects.journal_append(
        slug, messaging.current_sender(), "adopt",
        "adopted %s %s (%s) as orchestrator" % (rec["sid"], rec.get("title") or rec["sid"], rec.get("agent")),
        data={"sid": rec["sid"], "title": rec.get("title"), "agent": rec.get("agent")},
    )
    if args.json:
        dump_json(rec)
    else:
        print("%s %s (%s)" % (rec["sid"], rec.get("title") or rec["sid"], rec.get("agent")))
        if rebind:
            print("cleared the agent session id: the next hook from this terminal rebinds the record to the "
                  "session running there")
    return 0


def run(args):
    return {"start": _start, "show": _show, "adopt": _adopt}[args.orch_cmd](args)
