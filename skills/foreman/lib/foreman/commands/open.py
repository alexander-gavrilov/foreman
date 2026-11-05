"""foreman open: launch a new agent session in a terminal tab and register it (spec §4.2, §4.4, §4.5)."""
import os
import shutil

from foreman import limits, messaging, projects, routing, sessions, transports
from foreman.cliutil import CliError, add_project_arg, dump_json, read_text_arg, resolve_project
from foreman.transports._proc import TransportError
from foreman.transports.base import TerminalGone

KNOWN_AGENTS = ("claude", "antigravity", "codex")


def default_transport(env=None, which=None):
    """Pick the transport when --transport is omitted: $FOREMAN_TRANSPORT, else the caller's own terminal
    environment (ORCA_TERMINAL_HANDLE / HERDR_PANE_ID), else $ORCA_CLI_COMMAND or `orca` on PATH, else `herdr`."""
    env = os.environ if env is None else env
    which = shutil.which if which is None else which
    if env.get("FOREMAN_TRANSPORT"):
        return env["FOREMAN_TRANSPORT"]
    if env.get("ORCA_TERMINAL_HANDLE"):
        return "orca"
    if env.get("HERDR_PANE_ID"):
        return "herdr"
    if env.get("ORCA_CLI_COMMAND") or which("orca"):
        return "orca"
    if which("herdr"):
        return "herdr"
    raise CliError("no transport available (neither orca nor herdr found on PATH)")


def require_agent_handle(transport, handle):
    """Ensure `handle` on `transport` is an agent terminal (Orca agentIdentity / Herdr agent), never a plain shell.
    Returns the matching TermInfo when listed, or None if the transport cannot list yet."""
    try:
        infos = transport.list()
    except Exception:
        return None
    for info in infos:
        if info.handle == handle:
            if not info.agent:
                try:
                    transport.close(handle)
                except Exception:
                    pass
                raise CliError(
                    "terminal %s on %s is a plain shell (transport reports no agent there); "
                    "foreman refuses to rename or register a shell"
                    % (handle, getattr(transport, "kind", "transport")),
                    code=2,
                )
            return info
    return None


def _read_prompt(args):
    has_prompt = args.prompt is not None
    has_file = args.prompt_file is not None
    if has_prompt == has_file:
        raise CliError("pass exactly one of --prompt or --prompt-file", code=2)
    if has_prompt:
        return read_text_arg(args.prompt)
    try:
        with open(args.prompt_file, "r", encoding="utf-8") as fh:
            return fh.read().rstrip("\n")
    except OSError as exc:
        raise CliError("cannot read --prompt-file %s: %s" % (args.prompt_file, exc), code=2)


def open_session(project, purpose, prompt, title=None, kind=None, agent=None, model=None, effort=None,
                 transport_kind=None, owner="orchestrator", role="worker", replaces=None, journal=True):
    """Resolve routing/limits, launch on the transport, verify it is an agent terminal, register the session,
    rename the tab, and append a journal line. Returns (rec, note)."""
    if model and not agent:
        raise CliError("--model requires --agent", code=2)
    if agent and agent not in KNOWN_AGENTS:
        raise CliError("unknown agent %r (known: %s)" % (agent, ", ".join(KNOWN_AGENTS)), code=2)
    if role == "orchestrator" and agent and agent not in routing.ORCHESTRATOR_AGENTS:
        raise CliError("agent %r is worker-only in v1 and cannot be the orchestrator (use: %s)"
                       % (agent, ", ".join(routing.ORCHESTRATOR_AGENTS)), code=2)
    if role == "orchestrator":
        # A project has at most one live orchestrator; only a rotation (replaces=<it>) may succeed it.
        live = [r for r in sessions.list_sessions(project["slug"])
                if r.get("role") == "orchestrator" and sessions.is_live(r) and r["sid"] != replaces]
        if live:
            raise CliError("project %s already has a live orchestrator (%s); close or rotate it first"
                           % (project["slug"], live[-1]["sid"]), code=2)
    eff_kind = kind or ("orchestrator" if role == "orchestrator" else "routine")
    preferred = (agent, model, effort) if agent else None
    try:
        chosen_agent, chosen_model, chosen_effort, note = routing.choose(
            eff_kind, limits.levels(), preferred=preferred
        )
    except routing.RoutingError as exc:
        raise CliError(str(exc), code=5 if exc.providers else 2)
    if role == "orchestrator" and chosen_agent not in routing.ORCHESTRATOR_AGENTS:
        raise CliError("agent %r is worker-only in v1 and cannot be the orchestrator" % chosen_agent, code=2)

    tkind = transport_kind or default_transport()
    try:
        transport = transports.get_transport(tkind)
    except ValueError as exc:
        raise CliError(str(exc), code=2)

    launch_title = None if tkind in ("fake", "orca") else title
    try:
        handle = transport.launch(chosen_agent, chosen_model, chosen_effort, project["path"], prompt, launch_title)
    except NotImplementedError as exc:
        raise CliError(str(exc), code=2)
    except TransportError as exc:
        raise CliError(str(exc), code=1)

    require_agent_handle(transport, handle)

    rec = sessions.create_session(
        project=project["slug"], agent=chosen_agent, model=chosen_model, effort=chosen_effort,
        role=role, owner=owner, purpose=purpose, title=title,
        transport={"kind": tkind, "handle": handle},
        cwd=project["path"], state="starting", replaces=replaces, lifecycle="active",
    )
    if launch_title != rec["title"]:
        try:
            transport.rename(handle, rec["title"])
        except (TerminalGone, TransportError, NotImplementedError):
            pass

    if role == "orchestrator":
        proj = projects.load_project(project["slug"])
        if proj:
            proj["orchestrator"] = rec["sid"]
            projects.save_project(proj)

    if journal:
        am = chosen_agent + ("/" + chosen_model if chosen_model else "")
        line = "opened %s %s (%s)%s" % (rec["sid"], rec["title"], am, " — " + note if note else "")
        projects.journal_append(
            project["slug"], messaging.current_sender(), "open", line,
            data={"sid": rec["sid"], "title": rec["title"], "agent": chosen_agent, "model": chosen_model,
                  "effort": chosen_effort, "role": role, "owner": owner, "note": note},
        )
    return rec, note


def format_open_line(rec, note):
    am = (rec.get("agent") or "?") + ("/" + rec["model"] if rec.get("model") else "")
    return "%s %s (%s)%s" % (rec["sid"], rec["title"], am, " — " + note if note else "")


def register(subparsers):
    p = subparsers.add_parser("open", help="open a new agent session")
    add_project_arg(p)
    p.add_argument("--agent", help="claude | antigravity | codex")
    p.add_argument("--model", help="model name (requires --agent)")
    p.add_argument("--effort", help="reasoning effort level")
    p.add_argument("--kind", help="task kind for model routing (routine, research, design, review, hard, orchestrator)")
    p.add_argument("--purpose", required=True, help="one-line purpose of the session")
    p.add_argument("--title", help="tab and session title (default: the session id)")
    p.add_argument("--prompt", help="initial prompt ('-' reads stdin)")
    p.add_argument("--prompt-file", help="read initial prompt from this file")
    p.add_argument("--transport", help="orca | herdr | fake")
    p.add_argument("--owner", choices=("orchestrator", "user"), default="orchestrator")
    p.add_argument("--role", choices=("worker", "orchestrator"), default="worker")
    p.add_argument("--json", action="store_true")


def run(args):
    proj = resolve_project(args)
    prompt = _read_prompt(args)
    rec, note = open_session(
        project=proj, purpose=args.purpose, prompt=prompt, title=args.title,
        kind=args.kind, agent=args.agent, model=args.model, effort=args.effort,
        transport_kind=args.transport, owner=args.owner, role=args.role,
    )
    if args.json:
        dump_json(dict(rec, note=note))
    else:
        print(format_open_line(rec, note))
    return 0
