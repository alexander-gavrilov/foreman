"""foreman gate: autonomy gates of a project (spec §4.10). Exit 10 = ask the user first."""
from foreman.cliutil import add_project_arg, dump_json, resolve_project

EXIT_ASK = 10


def register(subparsers):
    p = subparsers.add_parser("gate", help="autonomy gates: list, check")
    sub = p.add_subparsers(dest="gate_cmd", metavar="<action>", required=True)
    l = sub.add_parser("list", help="gates configured for the project")
    add_project_arg(l)
    l.add_argument("--json", action="store_true")
    c = sub.add_parser("check", help="'ask the user' (exit 10) if the gate is configured, else 'proceed' (exit 0)")
    add_project_arg(c)
    c.add_argument("name")


def run(args):
    proj = resolve_project(args)
    gates = proj.get("gates") or []
    if args.gate_cmd == "list":
        if args.json:
            dump_json(gates)
        elif gates:
            for g in gates:
                print(g)
        else:
            print("(none configured)")
        return 0
    if args.name in gates:
        print("ask the user")
        return EXIT_ASK
    print("proceed")
    return 0
