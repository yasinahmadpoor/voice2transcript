"""Exercise real subprocess cancellation against a local, blocking audio API."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import override_settings

from apps.transcriptions.services import execution, jobs
from apps.transcriptions.services.queue import JobQueue


class IsolatedExecutionTests(unittest.TestCase):
    def test_termination_also_stops_conversion_children(self):
        child_script = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
        script = (
            "import subprocess,sys,time; "
            f"child=subprocess.Popen([sys.executable,'-c',{child_script!r}],stdout=subprocess.PIPE); "
            "child.stdout.readline(); print(child.pid,flush=True); time.sleep(60)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            child_pid = int(process.stdout.readline())
            execution._terminate(process)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = Path(f"/proc/{child_pid}/stat")
                if not state.exists() or state.read_text().split()[2] == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("Conversion child survived cancellation")
            self.assertIsNotNone(process.poll())
        finally:
            execution._terminate(process)
            process.stdout.close()

    def test_cancel_terminates_one_provider_request_frees_slot_and_cleans_files(self):
        first_started = threading.Event()
        second_started = threading.Event()
        release = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                if b"block-first" in body:
                    first_started.set()
                    release.wait(30)
                elif b"block-second" in body:
                    second_started.set()
                    release.wait(30)
                response = json.dumps({"text": "Local synthetic transcript"}).encode()
                try:
                    self.send_response(200)
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
        queue = None
        processes = []
        original_popen = subprocess.Popen

        def capture_process(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append((process, Path(kwargs["env"]["TMPDIR"])))
            return process

        def wait(predicate):
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.02)
            self.fail("Isolated worker did not reach expected state")

        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                root = Path(__file__).resolve().parent.parent
                environment = os.environ | {
                    "DJANGO_SETTINGS_MODULE": "config.settings.production",
                    "DJANGO_SECRET_KEY": "isolated-execution-test-key",
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
                provider_script = (
                    "import django; django.setup(); "
                    "from apps.transcriptions.models import ModelProvider; "
                    f"ModelProvider.objects.create(name='Local test',kind='openai',base_url='http://127.0.0.1:{server.server_port}/v1',model='test')"
                )
                subprocess.run(
                    [sys.executable, "-c", provider_script],
                    cwd=root,
                    env=environment,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
                queue = JobQueue(folder, jobs.run_pipeline, workers=2)

                def enqueue(model):
                    job_id = uuid4().hex
                    recording = queue.uploads / f"{job_id}.media"
                    with wave.open(str(recording), "wb") as audio:
                        audio.setnchannels(1)
                        audio.setsampwidth(2)
                        audio.setframerate(16000)
                        audio.writeframes(b"\0\0" * 1600)
                    parameters = {
                        "provider": "1",
                        "model": model,
                        "chunk_minutes": 0,
                        "language_hint": "",
                        "fallback_model": "",
                        "prompt": "Verbatim",
                    }
                    return queue.enqueue(
                        job_id, "owner", f"{model}.wav", recording, parameters
                    )

                with (
                    patch.dict(os.environ, environment),
                    override_settings(
                        SETTINGS_MODULE="config.settings.production", DATA_DIR=folder
                    ),
                    patch.object(
                        execution.subprocess, "Popen", side_effect=capture_process
                    ),
                ):
                    first = enqueue("block-first")
                    second = enqueue("block-second")
                    third = enqueue("quick-third")
                    queue.start()
                    try:
                        self.assertTrue(first_started.wait(12))
                        self.assertTrue(second_started.wait(12))
                        self.assertEqual(queue.list("owner")["active"], 2)
                        self.assertEqual(
                            queue.get(third["id"], "owner")["status"], "queued"
                        )
                        queue.cancel(first["id"], "owner")
                        wait(
                            lambda: queue.get(first["id"], "owner")["status"]
                            == "cancelled"
                        )
                        wait(
                            lambda: queue.get(third["id"], "owner")["status"]
                            == "completed"
                        )
                        self.assertEqual(
                            queue.result(third["id"], "owner"),
                            "Local synthetic transcript",
                        )
                        self.assertEqual(
                            queue.get(second["id"], "owner")["status"], "processing"
                        )
                        self.assertEqual(queue.list("owner")["active"], 1)
                        self.assertEqual(
                            sum(process.poll() is None for process, _ in processes), 1
                        )
                        queue.stop(timeout=5)
                        self.assertIsNone(queue._lock_file)
                        self.assertTrue(queue.get(second["id"], "owner")["interrupted"])
                        self.assertEqual(
                            queue.get(first["id"], "owner")["status"], "cancelled"
                        )
                        self.assertTrue(
                            all(process.poll() is not None for process, _ in processes)
                        )
                        self.assertTrue(
                            all(not scratch.exists() for _, scratch in processes)
                        )
                    finally:
                        queue.stop(timeout=5)
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            serving.join(timeout=5)
