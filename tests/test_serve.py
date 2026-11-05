"""Tests for foreman serve daemon: web server, watch loop, telegram poller, exception survival."""
import logging
import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import ForemanTestCase, run_cli


class ServeDaemonTest(ForemanTestCase):
    def test_serve_runs_watch_and_web(self):
        from foreman.commands import serve
        from foreman import watch

        stop_event = threading.Event()
        watch_calls = []

        def mock_watch_pass():
            watch_calls.append(time.time())
            if len(watch_calls) >= 2:
                stop_event.set()
            return {"status": "ok"}

        with patch.object(watch, "run_pass", side_effect=mock_watch_pass):
            # Port 0 for ephemeral port in tests
            t = threading.Thread(
                target=serve.run_daemon,
                kwargs={
                    "binds": ["127.0.0.1"],
                    "port": 0,
                    "watch_interval": 0.05,
                    "stop_event": stop_event,
                },
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(len(watch_calls), 2)

    def test_watch_thread_survives_exceptions(self):
        from foreman.commands import serve
        from foreman import watch

        stop_event = threading.Event()
        calls = []

        def failing_watch():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("simulated watch failure")
            stop_event.set()
            return {}

        with patch.object(watch, "run_pass", side_effect=failing_watch):
            t = threading.Thread(
                target=serve.run_daemon,
                kwargs={
                    "binds": ["127.0.0.1"],
                    "port": 0,
                    "watch_interval": 0.05,
                    "stop_event": stop_event,
                },
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(len(calls), 2)

    def test_telegram_thread_runs_when_configured(self):
        from foreman.commands import serve
        from foreman import store, paths, telegram

        # Configure telegram in secrets.json
        sec_path = paths.secrets_file()
        store.write_json(sec_path, {"telegram_bot_token": "fake-token"}, mode=0o600)

        stop_event = threading.Event()
        poll_calls = []

        def mock_poll(timeout=25):
            poll_calls.append(timeout)
            if len(poll_calls) >= 2:
                stop_event.set()
            return 0

        with patch.object(telegram, "poll_once", side_effect=mock_poll):
            t = threading.Thread(
                target=serve.run_daemon,
                kwargs={
                    "binds": ["127.0.0.1"],
                    "port": 0,
                    "watch_interval": 10.0,
                    "telegram_timeout": 0.05,
                    "stop_event": stop_event,
                },
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(len(poll_calls), 2)

    def test_telegram_thread_survives_exceptions(self):
        from foreman.commands import serve
        from foreman import store, paths, telegram

        store.write_json(paths.secrets_file(), {"telegram_bot_token": "fake-token"}, mode=0o600)

        stop_event = threading.Event()
        poll_calls = []

        def failing_poll(timeout=25):
            poll_calls.append(1)
            if len(poll_calls) == 1:
                raise ConnectionError("network blip")
            stop_event.set()
            return 0

        with patch.object(telegram, "poll_once", side_effect=failing_poll):
            t = threading.Thread(
                target=serve.run_daemon,
                kwargs={
                    "binds": ["127.0.0.1"],
                    "port": 0,
                    "watch_interval": 10.0,
                    "telegram_timeout": 0.05,
                    "stop_event": stop_event,
                },
                daemon=True,
            )
            t.start()
            t.join(timeout=5)

        self.assertFalse(t.is_alive())
        self.assertGreaterEqual(len(poll_calls), 2)
