"""foreman reply <msgid> <text|->: answer a `foreman ask`."""
from foreman import messaging
from foreman.cliutil import read_text_arg


def register(subparsers):
    p = subparsers.add_parser("reply", help="answer a question received through `foreman ask`")
    p.add_argument("msgid")
    p.add_argument("text", help="the answer ('-' reads stdin)")


def run(args):
    messaging.reply(args.msgid, read_text_arg(args.text))
    print("ok")
    return 0
