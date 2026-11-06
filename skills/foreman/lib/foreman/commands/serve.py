"""foreman serve: long-running daemon: web server, watch loop, Telegram poller (architecture §1, §8, §9)."""
import logging
import os
import signal
import sys
import threading
import time

from foreman import config, outbox, paths, store, telegram, watch
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


TELEGRAM_IDLE_S = 5.0  # how long the poller sleeps while Telegram is not configured (re-checked every pass)


def _telegram_loop(stop_event, timeout, idle=TELEGRAM_IDLE_S):
    """Always runs: the configuration is re-read on every pass, so `telegram set-token` / `set-chat` take effect
    without restarting the service."""
    logger.debug("Telegram poller thread started (timeout=%ss)", timeout)
    while not stop_event.is_set():
        try:
            if not (telegram.get_token() and telegram.get_chat_id()):
                stop_event.wait(idle)
                continue
            started = time.monotonic()
            n = telegram.poll_once(timeout=timeout)
            if not n and time.monotonic() - started < 0.2:  # API down / nothing long-polled: do not spin
                stop_event.wait(0.2)
        except Exception as exc:
            logger.exception("Error in telegram poller: %s", exc)
            stop_event.wait(1.0)


def run_daemon(binds=None, port=None, watch_interval=60.0, telegram_timeout=25.0, stop_event=None, on_ready=None,
               telegram_idle=TELEGRAM_IDLE_S):
    """Run web server, watch loop thread, and the Telegram poller until stop_event is set."""
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

    # Telegram poller thread: always started, configured or not (see _telegram_loop)
    tg_thread = threading.Thread(
        target=_telegram_loop, args=(stop_event, telegram_timeout, telegram_idle), name="foreman-serve-telegram",
        daemon=True
    )
    tg_thread.start()

    # D60: drains the Telegram mirror outbox every ~2 s (CLI processes leave their rows here)
    outbox_thread = outbox.start_drain_thread(stop_event)

    # Run web server
    try:
        server.run_server(binds=binds, port=port, stop_event=stop_event, on_ready=on_ready)
    finally:
        stop_event.set()
        watch_thread.join(timeout=2.0)
        tg_thread.join(timeout=2.0)
        outbox_thread.join(timeout=2.0)


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
