"""foreman uninstall: revert `foreman install` (state is kept unless --purge)."""
from foreman import install as machine
from foreman.cliutil import CliError

NAME = "uninstall"


def register(subparsers):
    p = subparsers.add_parser(NAME, help="revert `foreman install` (keeps state unless --purge)")
    p.add_argument("--purge", action="store_true", help="also delete the state folder (projects, sessions, findings)")


def run(args, platform=None, runner=None, home=None):
    try:
        changes = machine.uninstall(purge=args.purge, home=home, platform=platform, runner=runner)
    except machine.InstallError as exc:
        raise CliError(str(exc), 1)
    for line in changes:
        print(line)
    if not changes:
        print("nothing to remove")
    return 0
