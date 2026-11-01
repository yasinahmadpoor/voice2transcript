"""Resume real provider subprocesses using durable checkpoints and local HTTP."""

import json
import os
import re
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

from apps.transcriptions.services import jobs
from apps.transcriptions.services.queue import JobQueue


class ResumeProcessTests(unittest.TestCase):
    def test_resume_across_queue_restart_and_incompatible_provider_requires_restart(
        self,
    ):
        requests = []
        control = {"fail": True}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                index = int(re.search(rb'filename="chunk_(\d+)\.wav"', body)[1])
                requests.append(index)
                status = 200
                if index == 1 and control["fail"]:
                    control["fail"] = False
                    status = 503
                response = json.dumps({"text": f"Part {index}"}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        queue = None
        try:
            with tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                root = Path(__file__).resolve().parent.parent
                environment = os.environ | {
                    "DJANGO_SETTINGS_MODULE": "config.settings.production",
                    "DJANGO_SECRET_KEY": "resume-process-test-key",
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

                def configure(create):
                    action = (
                        f"ModelProvider.objects.create(name='Local',kind='openai',base_url='http://127.0.0.1:{server.server_port}/v1',model='test')"
                        if create
                        else f"ModelProvider.objects.update(base_url='http://127.0.0.1:{server.server_port}/changed/v1')"
                    )
                    subprocess.run(
                        [
                            sys.executable,
                            "-c",
                            "import django; django.setup(); from apps.transcriptions.models import ModelProvider; "
                            + action,
                        ],
                        cwd=root,
                        env=environment,
                        check=True,
                        stdout=subprocess.DEVNULL,
                    )

                configure(True)
                queue = JobQueue(folder, jobs.run_pipeline, workers=1)

                def enqueue():
                    path = queue.uploads / f"{uuid4().hex}.media"
                    with wave.open(str(path), "wb") as audio:
                        audio.setnchannels(1)
                        audio.setsampwidth(2)
                        audio.setframerate(16000)
                        audio.writeframes(b"\0\0" * 16000 * 3)
                    return queue.enqueue(
                        path.stem,
                        "owner",
                        "Resume.wav",
                        path,
                        {
                            "provider": "1",
                            "model": "test",
                            "chunk_minutes": 1 / 60,
                            "language_hint": "",
                            "fallback_model": "",
                            "prompt": "Verbatim",
                        },
                    )

                def wait(job_id, status):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        job = queue.get(job_id, "owner")
                        if job["status"] == status:
                            return job
                        time.sleep(0.02)
                    self.fail(f"Worker did not reach {status}")

                with (
                    patch.dict(os.environ, environment),
                    override_settings(
                        SETTINGS_MODULE="config.settings.production", DATA_DIR=folder
                    ),
                    patch("apps.transcriptions.services.queue.logger.exception"),
                ):
                    queue.start()
                    first = enqueue()
                    failed = wait(first["id"], "failed")
                    self.assertEqual(
                        failed["progress"], {"completed_chunks": 1, "total_chunks": 3}
                    )
                    queue.stop()
                    queue = JobQueue(folder, jobs.run_pipeline, workers=1)
                    queue.start()
                    self.assertTrue(queue.get(first["id"], "owner")["resumable"])
                    queue.retry(first["id"], "owner")
                    wait(first["id"], "completed")
                    self.assertEqual(requests, [0, 1, 1, 2])
                    self.assertEqual(
                        queue.result(first["id"], "owner"), "Part 0\n\nPart 1\n\nPart 2"
                    )
                    requests.clear()
                    control["fail"] = True
                    second = enqueue()
                    wait(second["id"], "failed")
                    configure(False)
                    queue.retry(second["id"], "owner")
                    mismatch = wait(second["id"], "failed")
                    self.assertTrue(mismatch["restart_required"])
                    self.assertEqual(requests, [0, 1])
                    queue.retry(second["id"], "owner", restart=True)
                    wait(second["id"], "completed")
                    self.assertEqual(requests, [0, 1, 0, 1, 2])
                    with queue._db() as db:
                        self.assertEqual(
                            db.execute(
                                "SELECT COUNT(*) FROM job_checkpoints"
                            ).fetchone()[0],
                            0,
                        )
                    queue.stop()
        finally:
            if queue is not None:
                queue.stop(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
