"""foreman serve: long-running daemon: web server, watch loop, Telegram poller (architecture §1, §8, §9)."""
import logging
import os
import signal
import sys
import threading

from foreman import config, paths, store, telegram, watch
from foreman.web import server

NAME = "serve"
logger = logging.getLogger(__name__)


def register(subparsers):
    p = subparsers.add_parser("serve", help="run the daemon: web UI, Telegram poller, watch loop")
    p.add_argument("--bind", action="append", help="bind address override")
    p.add_argument("--port", type=int, help="port override")
    p.add_argument("--watch-interval", type=float, default=60.0, help="seconds between watch loop passes (default: 60)")
    p.add_argument("--telegram-timeout", type=float, default=25.0, help="long-poll timeout for Telegram getUpdates")


def is_telegram_configured():
    cfg = config.load()
    if cfg.get("telegram", {}).get("chat_id"):
        return True
    sec = store.read_json(paths.secrets_file(), {})
    if isinstance(sec, dict) and sec.get("telegram_bot_token"):
        return True
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        return True
    if hasattr(telegram, "is_configured") and callable(telegram.is_configured):
        return telegram.is_configured()
    return False


def _watch_loop(stop_event, interval):
    logger.debug("Watch loop thread started (interval=%ss)", interval)
    while not stop_event.is_set():
        try:
            watch.run_pass()
        except Exception as exc:
            logger.exception("Error in watch loop pass: %s", exc)
        if stop_event.wait(interval):
            break


def _telegram_loop(stop_event, timeout):
    logger.debug("Telegram poller thread started (timeout=%ss)", timeout)
    while not stop_event.is_set():
        try:
            telegram.poll_once(timeout=timeout)
        except Exception as exc:
            logger.exception("Error in telegram poller: %s", exc)
            stop_event.wait(1.0)


def run_daemon(binds=None, port=None, watch_interval=60.0, telegram_timeout=25.0, stop_event=None, on_ready=None):
    """Run web server, watch loop thread, and telegram poller (if configured) until stop_event is set."""
    if stop_event is None:
        stop_event = threading.Event()

    cfg = config.load()
    if binds is None:
        binds = cfg.get("web", {}).get("bind", ["127.0.0.1"])
    if port is None:
        port = cfg.get("web", {}).get("port", 8765)

    # Start watch loop thread
    watch_thread = threading.Thread(
        target=_watch_loop, args=(stop_event, watch_interval), name="foreman-serve-watch", daemon=True
    )
    watch_thread.start()

    # Start telegram thread if configured
    tg_thread = None
    if is_telegram_configured():
        tg_thread = threading.Thread(
            target=_telegram_loop, args=(stop_event, telegram_timeout), name="foreman-serve-telegram", daemon=True
        )
        tg_thread.start()

    # Run web server
    try:
        server.run_server(binds=binds, port=port, stop_event=stop_event, on_ready=on_ready)
    finally:
        stop_event.set()
        watch_thread.join(timeout=2.0)
        if tg_thread:
            tg_thread.join(timeout=2.0)


def run(args):
    stop_event = threading.Event()

    def _handle_sig(sig, frame):
        stop_event.set()

    try:
        signal.signal(signal.SIGTERM, _handle_sig)
        signal.signal(signal.SIGINT, _handle_sig)
    except (ValueError, OSError):
        pass

    def _on_ready(servers):
        for s in servers:
            host, port = s.server_address[:2]
            print("foreman serve listening on http://%s:%d" % (host, port))
            sys.stdout.flush()

    binds = getattr(args, "bind", None)
    port = getattr(args, "port", None)
    watch_interval = getattr(args, "watch_interval", 60.0)
    telegram_timeout = getattr(args, "telegram_timeout", 25.0)

    run_daemon(
        binds=binds,
        port=port,
        watch_interval=watch_interval,
        telegram_timeout=telegram_timeout,
        stop_event=stop_event,
        on_ready=_on_ready,
    )
    return 0
