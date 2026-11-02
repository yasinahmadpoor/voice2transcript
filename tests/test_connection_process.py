"""Run admin probes through real subprocesses against a local-only fake API."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import override_settings

from apps.transcriptions.services import connection_test as probe
from apps.transcriptions.services import execution


class ConnectionProcessTests(unittest.TestCase):
    def test_probe_success_safe_failure_and_deadline_cleanup_without_jobs(self):
        requests = []
        release = threading.Event()
        control = {"status": 200, "block": False}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                requests.append(
                    (self.path, self.rfile.read(int(self.headers["Content-Length"])))
                )
                if control["block"]:
                    release.wait(20)
                response = json.dumps(
                    {"text": "Hello test", "error": "fake-private-response"}
                ).encode()
                try:
                    self.send_response(control["status"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        processes = []
        real_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append((process, Path(kwargs["env"]["TMPDIR"])))
            return process

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(__file__).resolve().parent.parent
                environment = os.environ | {
                    "DJANGO_SETTINGS_MODULE": "config.settings.production",
                    "DJANGO_SECRET_KEY": "connection-process-test-key",
                    "JOB_DATA_DIR": directory,
                    "APP_USERNAME": "",
                }
                subprocess.run(
                    [sys.executable, "manage.py", "migrate", "--noinput"],
                    cwd=root,
                    env=environment,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import django; django.setup(); from apps.transcriptions.models import ModelProvider; "
                        f"ModelProvider.objects.create(name='Local',kind='openai',base_url='http://127.0.0.1:{server.server_port}/v1',model='test',is_enabled=False)",
                    ],
                    cwd=root,
                    env=environment,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                with sqlite3.connect(Path(directory) / "jobs.sqlite3") as database:
                    tables_before = database.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    ).fetchall()
                with (
                    patch.dict(os.environ, environment),
                    override_settings(
                        SETTINGS_MODULE="config.settings.production",
                        DATA_DIR=Path(directory),
                    ),
                    patch.object(execution.subprocess, "Popen", side_effect=capture),
                ):
                    result = probe.check_connection(SimpleNamespace(pk=1))
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(len(requests), 1)
                    self.assertEqual(requests[0][0], "/v1/audio/transcriptions")
                    self.assertIn(b"RIFF", requests[0][1])
                    self.assertNotIn("Hello test", json.dumps(result))
                    control["status"] = 401
                    result = probe.check_connection(SimpleNamespace(pk=1))
                    self.assertFalse(result["ok"])
                    self.assertIn("Authentication", result["message"])
                    self.assertNotIn("fake-private-response", json.dumps(result))
                    control.update(status=200, block=True)
                    started = time.monotonic()
                    with patch.object(probe, "TEST_TIMEOUT_SECONDS", 8):
                        result = probe.check_connection(SimpleNamespace(pk=1))
                    self.assertFalse(result["ok"])
                    self.assertIn("timed out", result["message"])
                    self.assertLess(time.monotonic() - started, 11)
                    self.assertEqual(len(requests), 3)
                    for process, temporary in processes:
                        self.assertIsNotNone(process.poll())
                        self.assertFalse(temporary.exists())
                with sqlite3.connect(Path(directory) / "jobs.sqlite3") as database:
                    self.assertEqual(
                        database.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                        ).fetchall(),
                        tables_before,
                    )
                    # The unmanaged queue table is initialized lazily. Testing a
                    # connection must not even initialize a queue to create it.
                    self.assertNotIn(("jobs",), tables_before)
                    self.assertEqual(
                        database.execute(
                            "SELECT COUNT(*) FROM job_checkpoints"
                        ).fetchone()[0],
                        0,
                    )
                    self.assertEqual(
                        database.execute(
                            "SELECT is_enabled, is_default FROM web_modelprovider"
                        ).fetchone(),
                        (0, 0),
                    )
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            serving.join(timeout=5)
