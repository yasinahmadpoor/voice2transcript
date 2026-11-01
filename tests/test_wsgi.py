"""Exercise the real production entry point without contacting Gemini."""

import http.cookiejar
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from apps.accounts import services as auth


class WSGITests(unittest.TestCase):
    def test_gunicorn_queue_static_files_and_restart(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            release = folder / "release"
            # Only this test process replaces Gemini; production code is unchanged.
            (folder / "integration_wsgi.py").write_text(
                "import os, time\n"
                "from pathlib import Path\n"
                "from apps.transcriptions.services import jobs as services\n"
                "def runner(*args):\n"
                "    while not Path(os.environ['TEST_RELEASE']).exists():\n"
                "        time.sleep(0.02)\n"
                "    return 'SPEAKER_1: Synthetic transcript'\n"
                "services.run_pipeline = runner\n"
                "from config.wsgi import application\n"
            )
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            env = os.environ | {
                "APP_USERNAME": "integration",
                "APP_PASSWORD_HASH": auth.hash_password("integration-test-password"),
                "JOB_DATA_DIR": str(folder / "data"),
                "SECURE_COOKIES": "false",
                "DJANGO_ALLOWED_HOSTS": "127.0.0.1",
                "TRANSCRIPTION_WORKERS": "2",
                "MAX_PENDING_JOBS": "20",
                "TEST_RELEASE": str(release),
            }
            subprocess.run(
                [sys.executable, "manage.py", "migrate", "--noinput"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            client = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )

            def request(path, data=None, headers=None, opener=None):
                req = urllib.request.Request(
                    base + path, data=data, headers=headers or {}
                )
                try:
                    response = (opener or client).open(req, timeout=5)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, response.read(), response.headers

            def login():
                body = json.dumps(
                    {"username": "integration", "password": "integration-test-password"}
                ).encode()
                self.assertEqual(
                    request("/auth/login", body, {"Content-Type": "application/json"})[
                        0
                    ],
                    200,
                )

            def admin_token():
                status, body, _ = request("/admin/login/")
                self.assertEqual(status, 200)
                return re.search(
                    r'name="csrfmiddlewaretoken" value="([^"]+)"', body.decode()
                ).group(1)

            def admin_login():
                token = admin_token()
                body = urllib.parse.urlencode(
                    {
                        "username": "integration",
                        "password": "integration-test-password",
                        "csrfmiddlewaretoken": token,
                        "next": "/admin/",
                    }
                ).encode()
                status, page, _ = request(
                    "/admin/login/",
                    body,
                    {"Content-Type": "application/x-www-form-urlencoded"},
                )
                self.assertEqual(status, 200)
                self.assertIn(b"Voice to Transcript administration", page)
                self.assertNotIn(b'name="username"', page)

            def admin_action(action, selected):
                token = admin_token()
                body = urllib.parse.urlencode(
                    {
                        "csrfmiddlewaretoken": token,
                        "action": action,
                        "_selected_action": selected,
                        "index": "0",
                    }
                ).encode()
                return request(
                    "/admin/web/transcriptionjob/",
                    body,
                    {"Content-Type": "application/x-www-form-urlencoded"},
                )

            def create_person(username):
                body = urllib.parse.urlencode(
                    {
                        "csrfmiddlewaretoken": admin_token(),
                        "username": username,
                        "usable_password": "true",
                        "password1": "test-person-private-password",
                        "password2": "test-person-private-password",
                        "_save": "Save",
                    }
                ).encode()
                status, page, _ = request(
                    "/admin/auth/user/add/",
                    body,
                    {"Content-Type": "application/x-www-form-urlencoded"},
                )
                self.assertEqual(status, 200)
                self.assertIn(b"was added successfully", page)
                opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
                )
                status, _, _ = request(
                    "/auth/login",
                    json.dumps(
                        {
                            "username": username,
                            "password": "test-person-private-password",
                        }
                    ).encode(),
                    {"Content-Type": "application/json"},
                    opener,
                )
                self.assertEqual(status, 200)
                self.assertIn(b"Log in", request("/admin/", opener=opener)[1])
                return opener

            def upload_for(opener, filename):
                body = (
                    '--test-boundary\r\nContent-Disposition: form-data; name="file"; '
                    f'filename="{filename}"\r\nContent-Type: audio/wav\r\n\r\n'
                    "synthetic-audio\r\n--test-boundary--\r\n"
                ).encode()
                status, payload, _ = request(
                    "/jobs",
                    body,
                    {
                        "Content-Type": "multipart/form-data; boundary=test-boundary",
                        "Idempotency-Key": "shared-person-key",
                    },
                    opener,
                )
                self.assertEqual(status, 202)
                return json.loads(payload)["id"]

            def wait_for(predicate):
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    try:
                        if predicate():
                            return
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.05)
                self.fail("Timed out waiting for the WSGI server or queue")

            with (folder / "server.log").open("w+") as log:
                command = [
                    sys.executable,
                    "-m",
                    "gunicorn",
                    "integration_wsgi:application",
                    "--config",
                    str(root / "gunicorn.conf.py"),
                    "--pythonpath",
                    str(folder),
                    "--bind",
                    f"127.0.0.1:{port}",
                    "--access-logfile",
                    os.devnull,
                ]
                process = None
                try:
                    process = subprocess.Popen(
                        command, cwd=root, env=env, stdout=log, stderr=log
                    )
                    wait_for(lambda: request("/health")[0] == 200)
                    for path in (
                        "/",
                        "/static/transcriptions/app.js",
                        "/static/css/site.css",
                        "/static/js/theme.js",
                    ):
                        status, body, headers = request(path)
                        self.assertEqual(status, 200)
                        self.assertTrue(body)
                        self.assertIn(
                            "frame-ancestors 'none'", headers["Content-Security-Policy"]
                        )
                    self.assertEqual(request("/jobs")[0], 401)
                    self.assertIn(b"Log in", request("/admin/")[1])
                    self.assertEqual(request("/static/admin/css/base.css")[0], 200)
                    self.assertEqual(request("/static/admin/js/theme.js")[0], 200)
                    self.assertEqual(
                        request(
                            "/admin/login/",
                            b"username=integration",
                            {"Content-Type": "application/x-www-form-urlencoded"},
                        )[0],
                        403,
                    )
                    login()
                    admin_login()
                    ids = []
                    for index in range(3):
                        body = (
                            '--test-boundary\r\nContent-Disposition: form-data; name="file"; '
                            f'filename="recording-{index}.wav"\r\nContent-Type: audio/wav\r\n\r\n'
                            "synthetic-audio\r\n--test-boundary--\r\n"
                        ).encode()
                        status, payload, _ = request(
                            "/jobs",
                            body,
                            {
                                "Content-Type": "multipart/form-data; boundary=test-boundary"
                            },
                        )
                        self.assertEqual(status, 202)
                        ids.append(json.loads(payload)["id"])
                    wait_for(lambda: json.loads(request("/jobs")[1])["active"] == 2)
                    self.assertEqual(json.loads(request("/jobs")[1])["queued"], 1)
                    status, listing, _ = request("/admin/web/transcriptionjob/")
                    self.assertEqual(status, 200)
                    self.assertIn(b"recording-0.wav", listing)
                    # Queue actions cannot delete processing jobs or edit worker state.
                    self.assertEqual(admin_action("remove_recordings", "1")[0], 200)
                    self.assertEqual(len(json.loads(request("/jobs")[1])["jobs"]), 3)
                    self.assertEqual(
                        request("/admin/web/transcriptionjob/add/")[0], 403
                    )
                    release.touch()
                    wait_for(
                        lambda: all(
                            job["status"] == "completed"
                            for job in json.loads(request("/jobs")[1])["jobs"]
                        )
                    )
                    alice = create_person("alice")
                    bob = create_person("bob")
                    alice_job = upload_for(alice, "alice-private.wav")
                    bob_job = upload_for(bob, "bob-private.wav")
                    self.assertNotEqual(alice_job, bob_job)
                    wait_for(
                        lambda: (
                            json.loads(request(f"/jobs/{alice_job}", opener=alice)[1])[
                                "status"
                            ]
                            == "completed"
                        )
                    )
                    wait_for(
                        lambda: (
                            json.loads(request(f"/jobs/{bob_job}", opener=bob)[1])[
                                "status"
                            ]
                            == "completed"
                        )
                    )
                    for opener, own_job, hidden_job in (
                        (alice, alice_job, bob_job),
                        (bob, bob_job, alice_job),
                    ):
                        self.assertEqual(
                            [
                                job["id"]
                                for job in json.loads(
                                    request("/jobs", opener=opener)[1]
                                )["jobs"]
                            ],
                            [own_job],
                        )
                        self.assertEqual(
                            request(f"/jobs/{hidden_job}", opener=opener)[0], 404
                        )
                        self.assertEqual(
                            request(f"/jobs/{hidden_job}/result", opener=opener)[0], 404
                        )
                    process.terminate()
                    process.wait(timeout=10)
                    self.assertEqual(process.returncode, 0)
                    process = subprocess.Popen(
                        command, cwd=root, env=env, stdout=log, stderr=log
                    )
                    wait_for(lambda: request("/health")[0] == 200)
                    self.assertEqual(request("/auth/me")[0], 200)
                    # Native Django sessions persist with the signing key.
                    self.assertIn(
                        b"Voice to Transcript administration", request("/admin/")[1]
                    )
                    self.assertNotIn(b'name="username"', request("/admin/")[1])
                    login()
                    self.assertEqual(
                        {job["id"] for job in json.loads(request("/jobs")[1])["jobs"]},
                        set(ids),
                    )
                    self.assertEqual(
                        [
                            job["id"]
                            for job in json.loads(request("/jobs", opener=alice)[1])[
                                "jobs"
                            ]
                        ],
                        [alice_job],
                    )
                    self.assertEqual(
                        [
                            job["id"]
                            for job in json.loads(request("/jobs", opener=bob)[1])[
                                "jobs"
                            ]
                        ],
                        [bob_job],
                    )
                    self.assertEqual(request("/auth/logout", b"", opener=alice)[0], 204)
                    self.assertEqual(request("/auth/me", opener=alice)[0], 401)
                    self.assertEqual(request("/auth/me", opener=bob)[0], 200)
                    for job_id in ids:
                        status, transcript, _ = request(f"/jobs/{job_id}/result")
                        self.assertEqual(status, 200)
                        self.assertEqual(
                            transcript.decode(), "SPEAKER_1: Synthetic transcript"
                        )
                    self.assertEqual(admin_action("remove_recordings", "3")[0], 200)
                    self.assertEqual(len(json.loads(request("/jobs")[1])["jobs"]), 2)
                finally:
                    release.touch()
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
