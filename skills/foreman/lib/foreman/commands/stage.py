"""foreman stage <task> <stage> [--done]: a worker's one-line report to its orchestrator."""
from foreman import messaging
from foreman.cliutil import add_project_arg


def register(subparsers):
    p = subparsers.add_parser("stage", help="report a task's stage to the orchestrator (it owns the board)")
    add_project_arg(p)
    p.add_argument("task", help="board task id or title")
    p.add_argument("stage", help="the stage reached")
    p.add_argument("--done", action="store_true", help="the task is finished at this stage")


def run(args):
    messaging.stage(args.task, args.stage, done=args.done, project=args.project)
    print("ok")
    return 0
