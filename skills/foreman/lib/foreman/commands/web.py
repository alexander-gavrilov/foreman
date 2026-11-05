"""foreman web: start the web UI / pair a device (architecture §9)."""
import logging
import signal
import sys
import threading

from foreman import config
from foreman.cliutil import CliError, dump_json
from foreman.web import auth, server

NAME = "web"
logger = logging.getLogger(__name__)


def register(subparsers):
    p = subparsers.add_parser("web", help="start the web UI / pair a device")
    sub = p.add_subparsers(dest="web_cmd", metavar="<action>")

    # pair
    pr = sub.add_parser("pair", help="generate a one-time device pairing URL")
    pr.add_argument("--label", help="device label")
    pr.add_argument("--json", action="store_true")

    # devices
    dev = sub.add_parser("devices", help="list paired web devices")
    dev.add_argument("--json", action="store_true")

    # revoke
    rev = sub.add_parser("revoke", help="revoke a paired web device")
    rev.add_argument("device_id")
    rev.add_argument("--json", action="store_true")

    # serve (explicit subcommand, or default when no action given)
    srv = sub.add_parser("serve", help="run the web server")
    srv.add_argument("--bind", action="append", help="bind address override")
    srv.add_argument("--port", type=int, help="port override")


def _pair(args):
    code, url = auth.create_pairing_code(label=getattr(args, "label", None))
    if getattr(args, "json", False):
        dump_json({"code": code, "url": url})
    else:
        print(url)
    return 0


def _devices(args):
    devs = auth.list_devices()
    if getattr(args, "json", False):
        dump_json(devs)
        return 0
    if not devs:
        print("no paired devices")
        return 0
    for d in devs:
        print("%s  %s  %s" % (d["id"], d.get("created_at", ""), d.get("label", "")))
    return 0


def _revoke(args):
    dev_id = args.device_id
    if not auth.revoke_device(dev_id):
        raise CliError("device not found: %s" % dev_id)
    if getattr(args, "json", False):
        dump_json({"ok": True, "revoked": dev_id})
    else:
        print("revoked %s" % dev_id)
    return 0


def _serve(args):
    cfg = config.load()
    binds = getattr(args, "bind", None) or cfg.get("web", {}).get("bind", ["127.0.0.1"])
    port = getattr(args, "port", None) or cfg.get("web", {}).get("port", 8765)

    stop_event = threading.Event()

    def _handle_sig(sig, frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGTERM, _handle_sig)
        signal.signal(signal.SIGINT, _handle_sig)
    except (ValueError, OSError):
        pass  # in non-main threads in some test harnesses

    def _on_ready(servers):
        for s in servers:
            host, port_bound = s.server_address[:2]
            print("foreman web listening on http://%s:%d" % (host, port_bound))
            sys.stdout.flush()

    server.run_server(binds=binds, port=port, stop_event=stop_event, on_ready=_on_ready)
    return 0


def run(args):
    action = getattr(args, "web_cmd", None)
    if action == "pair":
        return _pair(args)
    if action == "devices":
        return _devices(args)
    if action == "revoke":
        return _revoke(args)
    # Default (no subcommand or "serve"): run the web server
    return _serve(args)
