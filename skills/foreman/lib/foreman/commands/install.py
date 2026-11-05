"""foreman install: set foreman up on this machine (safe to run again)."""
from foreman import install as machine
from foreman.cliutil import CliError

NAME = "install"


def register(subparsers):
    p = subparsers.add_parser(NAME, help="install foreman on this machine (safe to run again)")
    p.add_argument("--machine-kind", choices=("home", "work"), help="home (default on Linux) or work (macOS)")
    p.add_argument("--bind", action="append", metavar="ADDR", help="web UI bind address (repeatable)")
    p.add_argument("--port", type=int, help="web UI port")
    p.add_argument("--base-url", help="URL used in notification links, e.g. http://host.lan:8765")


def run(args, platform=None, runner=None, home=None):
    try:
        changes = machine.install(machine_kind=args.machine_kind, bind=args.bind, port=args.port,
                                  base_url=args.base_url, home=home, platform=platform, runner=runner)
    except machine.InstallError as exc:
        raise CliError(str(exc), 1)
    real = [c for c in changes if not c.startswith("service-manager commands skipped")]
    for line in changes:
        print(line)
    if not real:
        print("already correct")
    print("pair a browser or phone with: %s web pair" % machine.foreman_bin(home))
    print("next: run `foreman init` in each project")
    return 0
