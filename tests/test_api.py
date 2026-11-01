import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.sessions.models import Session
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TransactionTestCase, override_settings
from django.utils import timezone

from apps.accounts import services as auth
from apps.transcriptions.services import jobs as api
from apps.transcriptions.services import runtime


class WebTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "a-test-password-12345"
        cls.password_hash = auth.hash_password(cls.password)
        cls.django_password_hash = make_password(cls.password)

    def setUp(self):
        self.env = patch.dict(
            os.environ,
            {
                "APP_USERNAME": "tester",
                "APP_PASSWORD_HASH": self.password_hash,
                "SECURE_COOKIES": "false",
                "MAX_UPLOAD_MB": "1",
            },
        )
        self.env.start()
        self.data_dir = tempfile.TemporaryDirectory()
        self.jobs_env = patch.dict(
            os.environ,
            {
                "JOB_DATA_DIR": self.data_dir.name,
                "TRANSCRIPTION_WORKERS": "2",
                "MAX_PENDING_JOBS": "20",
            },
        )
        self.jobs_env.start()
        auth._attempts.clear()
        self.settings_override = override_settings(
            ALLOWED_HOSTS=["testserver", "localhost"]
        )
        self.settings_override.enable()
        self.user = get_user_model().objects.create(
            username="tester", password=self.django_password_hash
        )
        self.owner = auth.owner_key(self.user)
        self.queue = runtime.get_queue()
        self.client = Client()

    def tearDown(self):
        runtime.stop_queue()
        self.settings_override.disable()
        self.jobs_env.stop()
        self.data_dir.cleanup()
        self.env.stop()

    def login(self):
        return self.client.post(
            "/auth/login",
            data=json.dumps({"username": "tester", "password": self.password}),
            content_type="application/json",
        )

    def test_public_frontend_and_assets(self):
        for path in (
            "/",
            "/static/transcriptions/app.js",
            "/static/transcriptions/styles.css",
            "/static/transcriptions/theme.js",
            "/health",
            "/docs",
            "/openapi.json",
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(
                "frame-ancestors 'none'", response.headers["content-security-policy"]
            )
            response.close()

    def test_validation_and_json_errors(self):
        for data in (b"{", b"[]", b"null", b'{"username":1,"password":"test"}'):
            response = self.client.post(
                "/auth/login", data=data, content_type="application/json"
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/missing").json(), {"detail": "Not Found"})
        response = self.client.get("/auth/login")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers["allow"], "POST")
        self.assertEqual(
            self.client.get("/health", HTTP_HOST="untrusted.example").status_code, 400
        )
        self.login()
        for value in ("0", "101", "abc"):
            self.assertEqual(self.client.get(f"/jobs?limit={value}").status_code, 422)
        for key in ("", "invalid key", "x" * 129):
            response = self.client.post(
                "/jobs",
                headers={"Idempotency-Key": key},
                data={"file": SimpleUploadedFile("voice.wav", b"audio")},
            )
            self.assertEqual(response.status_code, 422)
        for value in ("nan", "inf", "121", "-1"):
            response = self.client.post(
                "/jobs",
                data={
                    "file": SimpleUploadedFile("voice.wav", b"audio"),
                    "chunk_minutes": value,
                },
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.post("/jobs").status_code, 422)
        self.assertEqual(list(self.queue.uploads.iterdir()), [])

    def test_hostile_browser_writes_and_static_paths(self):
        for headers in (
            {"Referer": "https://other.example/form"},
            {"Sec-Fetch-Site": "cross-site"},
        ):
            self.assertEqual(
                self.client.post("/auth/logout", headers=headers).status_code, 403
            )
        self.assertEqual(self.client.get("/static/../common/environment.py").status_code, 404)
        self.assertEqual(self.client.get("/static/missing.js").status_code, 404)

    def test_trailing_slashes_and_oversized_upload_cleanup(self):
        self.login()
        with patch.object(api, "run_pipeline", return_value="Transcript"):
            response = self.client.post(
                "/jobs/", data={"file": SimpleUploadedFile("voice.wav", b"audio")}
            )
            self.assertEqual(response.status_code, 202)
            self.wait_for_job(response.json()["id"])
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(FILE_UPLOAD_TEMP_DIR=directory),
        ):
            response = self.client.post(
                "/jobs",
                data={"file": SimpleUploadedFile("big.wav", b"x" * (1024 * 1024 + 1))},
            )
            self.assertEqual(response.status_code, 413)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(len(self.queue.list(self.owner)["jobs"]), 1)

    def test_management_checks_do_not_start_another_queue(self):
        from django.core.management import call_command

        with patch.object(runtime, "get_queue") as start, patch("sys.stdout"):
            call_command("check")
        start.assert_not_called()

    def test_anonymous_requests_are_blocked_before_pipeline(self):
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/transcription/defaults").status_code, 401)
        for path in ("/jobs", "/jobs/missing", "/jobs/missing/result"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(
            self.client.post(
                "/jobs",
                data=b"invalid multipart",
                content_type="application/octet-stream",
            ).status_code,
            401,
        )
        self.assertEqual(self.client.post("/jobs/missing/retry").status_code, 401)
        self.assertEqual(self.client.delete("/jobs/missing").status_code, 401)
        with patch.object(api, "run_pipeline") as pipeline:
            self.assertEqual(
                self.client.post(
                    "/transcribe",
                    data=b"invalid multipart",
                    content_type="application/octet-stream",
                ).status_code,
                401,
            )
            self.assertEqual(
                self.client.post(
                    "/transcribe/",
                    data={"file": SimpleUploadedFile("voice.wav", b"audio")},
                ).status_code,
                401,
            )
            pipeline.assert_not_called()

    def test_wrong_password_and_username(self):
        for username, password in (("tester", "wrong"), ("wrong", self.password)):
            response = self.client.post(
                "/auth/login",
                data=json.dumps({"username": username, "password": password}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(auth.COOKIE_NAME, response.cookies)

    def test_session_logout_and_revocation(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.cookies[auth.COOKIE_NAME].OutputString()
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        token = self.client.cookies[auth.COOKIE_NAME].value
        self.assertEqual(
            self.client.get("/auth/me").json(),
            {"username": "tester", "user_id": self.user.pk, "max_upload_mb": 1},
        )
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)
        self.client.cookies[auth.COOKIE_NAME] = token
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_expired_and_forged_sessions(self):
        self.client.cookies[auth.COOKIE_NAME] = "forged"
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.login()
        Session.objects.filter(
            session_key=self.client.cookies[auth.COOKIE_NAME].value
        ).update(expire_date=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_secure_cookie_setting(self):
        with override_settings(SESSION_COOKIE_SECURE=True):
            self.assertIn(
                "Secure", self.login().cookies[auth.COOKIE_NAME].OutputString()
            )

    def test_cross_site_writes_rejected(self):
        response = self.client.post(
            "/auth/login",
            headers={"Origin": "https://other.example"},
            data=json.dumps({"username": "tester", "password": self.password}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.post("/auth/logout", headers={"Origin": "null"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/auth/logout", headers={"Origin": "http://testserver"}
            ).status_code,
            204,
        )

    def test_rate_limit(self):
        for _ in range(5):
            self.client.post(
                "/auth/login",
                data=json.dumps({"username": "tester", "password": "wrong"}),
                content_type="application/json",
            )
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_django_accounts_work_without_legacy_env_credentials(self):
        with patch.dict(os.environ, {"APP_PASSWORD_HASH": ""}):
            self.assertEqual(self.login().status_code, 200)

    def submit_completed_job(self, client=None, key=None):
        client = client or self.client
        with patch.object(api, "run_pipeline", return_value="Private transcript"):
            response = client.post(
                "/jobs",
                data={"file": SimpleUploadedFile("private.wav", b"audio")},
                headers={"Idempotency-Key": key} if key else {},
            )
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]
            deadline = time.monotonic() + 5
            while client.get(f"/jobs/{job_id}").json()["status"] != "completed":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
        return job_id

    def test_separate_users_have_private_jobs_and_retry_keys(self):
        self.login()
        first_id = self.submit_completed_job(key="same-upload-key")
        other = get_user_model().objects.create(
            username="person-two", password=self.django_password_hash
        )
        second = Client()
        response = second.post(
            "/auth/login",
            data=json.dumps({"username": other.username, "password": self.password}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(other.is_staff)
        self.assertEqual(second.get("/jobs").json()["jobs"], [])
        second_id = self.submit_completed_job(second, "same-upload-key")
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(
            [job["id"] for job in self.client.get("/jobs").json()["jobs"]], [first_id]
        )
        self.assertEqual(
            [job["id"] for job in second.get("/jobs").json()["jobs"]], [second_id]
        )
        for client, hidden in ((self.client, second_id), (second, first_id)):
            for path in (f"/jobs/{hidden}", f"/jobs/{hidden}/result"):
                self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.post(f"/jobs/{hidden}/retry").status_code, 404)
            self.assertEqual(client.delete(f"/jobs/{hidden}").status_code, 404)

    def test_user_rename_retains_history_without_giving_it_to_reused_username(self):
        self.login()
        job_id = self.submit_completed_job()
        self.user.username = "renamed"
        self.user.save(update_fields=["username"])
        self.assertEqual(self.client.get("/auth/me").json()["username"], "renamed")
        self.assertEqual(
            self.client.get(f"/jobs/{job_id}/result").text, "Private transcript"
        )
        replacement = get_user_model().objects.create(
            username="tester", password=self.django_password_hash
        )
        second = Client()
        second.force_login(replacement)
        self.assertEqual(second.get("/jobs").json()["jobs"], [])
        self.assertEqual(second.get(f"/jobs/{job_id}").status_code, 404)

    def test_deleted_and_recreated_user_does_not_inherit_recordings(self):
        self.login()
        job_id = self.submit_completed_job()
        original_pk = self.user.pk
        self.user.delete()
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        replacement = get_user_model().objects.create(
            username="tester", password=self.django_password_hash
        )
        self.assertNotEqual(replacement.pk, original_pk)
        self.client.force_login(replacement)
        self.assertEqual(self.client.get("/jobs").json()["jobs"], [])
        self.assertEqual(self.client.get(f"/jobs/{job_id}/result").status_code, 404)
        self.assertEqual(self.queue.get(job_id, self.owner)["status"], "completed")

    def test_admin_password_changes_revoke_sessions_and_allow_new_password(self):
        self.login()
        self.user.set_password("a-new-private-password")
        self.user.save(update_fields=["password"])
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        response = self.client.post(
            "/auth/login",
            data=json.dumps(
                {"username": "tester", "password": "a-new-private-password"}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_deactivated_users_lose_access_and_cannot_upload(self):
        self.login()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/jobs",
                data=b"invalid multipart",
                content_type="application/octet-stream",
            ).status_code,
            401,
        )
        self.assertEqual(self.login().status_code, 401)
        self.assertEqual(list(self.queue.uploads.iterdir()), [])

    def test_switching_accounts_rotates_and_revokes_previous_session(self):
        self.login()
        old_token = self.client.cookies[auth.COOKIE_NAME].value
        other = get_user_model().objects.create(
            username="person-two", password=self.django_password_hash
        )
        response = self.client.post(
            "/auth/login",
            data=json.dumps({"username": other.username, "password": self.password}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.client.cookies[auth.COOKIE_NAME].value, old_token)
        self.assertEqual(self.client.get("/auth/me").json()["user_id"], other.pk)
        stale = Client()
        stale.cookies[auth.COOKIE_NAME] = old_token
        self.assertEqual(stale.get("/auth/me").status_code, 401)

    def test_regular_users_can_use_frontend_without_admin_access(self):
        self.login()
        self.assertEqual(self.client.get("/auth/me").status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertFalse(self.user.is_staff)

    def test_django_username_length_is_supported(self):
        username = "a" * 150
        get_user_model().objects.create(
            username=username, password=self.django_password_hash
        )
        response = self.client.post(
            "/auth/login",
            data=json.dumps({"username": username, "password": self.password}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_authenticated_upload_language_and_cleanup(self):
        self.login()
        seen_paths = []

        def fake_pipeline(path, model, chunk_minutes, language, fallback, prompt):
            self.assertEqual(path.read_bytes(), b"audio-content")
            self.assertEqual(language, "fa")
            seen_paths.append(path)
            return "SPEAKER_1: سلام دنیا"

        with patch.object(api, "run_pipeline", side_effect=fake_pipeline):
            response = self.client.post(
                "/transcribe",
                data={
                    "file": SimpleUploadedFile("recording.wav", b"audio-content"),
                    "language_hint": "fa",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "SPEAKER_1: سلام دنیا")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertTrue(seen_paths)
        self.assertFalse(seen_paths[0].exists())

    def test_form_defaults_match_transcribe_api(self):
        self.login()
        defaults = self.client.get("/transcription/defaults").json()
        schema = self.client.get("/openapi.json").json()
        body_ref = schema["paths"]["/transcribe"]["post"]["requestBody"]["content"][
            "multipart/form-data"
        ]["schema"]["$ref"]
        fields = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]][
            "properties"
        ]
        self.assertEqual(set(defaults), set(fields) - {"file"})
        for name, value in defaults.items():
            self.assertEqual(value, fields[name]["default"])

    def test_custom_settings_reach_pipeline(self):
        self.login()
        settings = {
            "model": "custom-primary",
            "fallback_model": "custom-fallback",
            "chunk_minutes": "7.5",
            "language_hint": "Italian",
            "prompt": "Transcribe verbatim.\nKeep speaker names.",
        }
        with patch.object(
            api, "run_pipeline", return_value="Custom transcript"
        ) as pipeline:
            response = self.client.post(
                "/transcribe",
                data={"file": SimpleUploadedFile("voice.wav", b"audio"), **settings},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            pipeline.call_args.args[1:],
            ("custom-primary", 7.5, "Italian", "custom-fallback", settings["prompt"]),
        )

    def test_disabled_chunking_fallback_and_auto_language(self):
        self.login()
        with patch.object(api, "run_pipeline", return_value="Transcript") as pipeline:
            response = self.client.post(
                "/transcribe",
                data={
                    "file": SimpleUploadedFile("voice.wav", b"audio"),
                    "chunk_minutes": "0",
                    "fallback_model": "",
                    "language_hint": "",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pipeline.call_args.args[2:5], (0, "", ""))

    def test_invalid_uploads_do_not_run_pipeline(self):
        self.login()
        with patch.object(api, "run_pipeline") as pipeline:
            self.assertEqual(
                self.client.post(
                    "/transcribe", data={"file": SimpleUploadedFile("empty.wav", b"")}
                ).status_code,
                400,
            )
            self.assertEqual(
                self.client.post(
                    "/transcribe",
                    data={
                        "file": SimpleUploadedFile("big.wav", b"a" * (1024 * 1024 + 1))
                    },
                ).status_code,
                413,
            )
            self.assertEqual(
                self.client.post(
                    "/transcribe",
                    data={
                        "file": SimpleUploadedFile("voice.wav", b"audio"),
                        "chunk_minutes": "-1",
                    },
                ).status_code,
                422,
            )
            pipeline.assert_not_called()

    def test_pipeline_error_does_not_expose_internal_details(self):
        self.login()
        with (
            patch.object(
                api,
                "run_pipeline",
                side_effect=RuntimeError("private-key-or-server-detail"),
            ),
            patch("apps.transcriptions.services.queue.logger.exception"),
        ):
            response = self.client.post(
                "/transcribe", data={"file": SimpleUploadedFile("voice.wav", b"audio")}
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-key-or-server-detail", response.text)

    def wait_for_job(self, job_id):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = self.client.get(f"/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed"}:
                return job
            time.sleep(0.02)
        self.fail("Job did not finish")

    def test_background_job_status_result_and_delete(self):
        self.login()
        with patch.object(
            api, "run_pipeline", return_value="SPEAKER_1: Background result"
        ):
            response = self.client.post(
                "/jobs", data={"file": SimpleUploadedFile("voice.wav", b"audio")}
            )
            self.assertEqual(response.status_code, 202)
            job_id = response.json()["id"]
            self.assertEqual(response.headers["location"], f"/jobs/{job_id}")
            self.assertEqual(self.wait_for_job(job_id)["status"], "completed")
        listing = self.client.get("/jobs").json()
        self.assertEqual(listing["max_active"], 2)
        self.assertEqual(listing["jobs"][0]["id"], job_id)
        result = self.client.get(f"/jobs/{job_id}/result")
        self.assertEqual(result.text, "SPEAKER_1: Background result")
        self.assertIn("attachment", result.headers["content-disposition"])
        self.assertEqual(self.client.delete(f"/jobs/{job_id}").status_code, 204)
        self.assertEqual(self.client.get(f"/jobs/{job_id}").status_code, 404)

    def test_job_results_are_scoped_to_account(self):
        self.login()
        with patch.object(api, "run_pipeline", return_value="Private result"):
            job_id = self.client.post(
                "/jobs", data={"file": SimpleUploadedFile("voice.wav", b"audio")}
            ).json()["id"]
            self.wait_for_job(job_id)
        other = get_user_model().objects.create(
            username="another-account", password=self.django_password_hash
        )
        self.client.force_login(other)
        with self.subTest("another account cannot access the first account jobs"):
            self.assertEqual(self.client.get("/jobs").json()["jobs"], [])
            self.assertEqual(self.client.get(f"/jobs/{job_id}").status_code, 404)
            self.assertEqual(self.client.get(f"/jobs/{job_id}/result").status_code, 404)
            self.assertEqual(self.client.post(f"/jobs/{job_id}/retry").status_code, 404)
            self.assertEqual(self.client.delete(f"/jobs/{job_id}").status_code, 404)

    def test_failed_job_can_retry_without_uploading_again(self):
        self.login()
        with (
            patch.object(
                api, "run_pipeline", side_effect=RuntimeError("private provider error")
            ),
            patch("apps.transcriptions.services.queue.logger.exception"),
        ):
            job_id = self.client.post(
                "/jobs", data={"file": SimpleUploadedFile("voice.wav", b"audio")}
            ).json()["id"]
            failed = self.wait_for_job(job_id)
        self.assertTrue(failed["retryable"])
        self.assertNotIn("private provider error", str(failed))
        self.assertEqual(self.client.get(f"/jobs/{job_id}/result").status_code, 409)
        with patch.object(api, "run_pipeline", return_value="Retry result"):
            self.assertEqual(self.client.post(f"/jobs/{job_id}/retry").status_code, 202)
            self.assertEqual(self.wait_for_job(job_id)["status"], "completed")
        self.assertEqual(self.client.post(f"/jobs/{job_id}/retry").status_code, 409)

    def test_background_and_legacy_requests_share_the_two_worker_limit(self):
        self.login()
        release = threading.Event()
        two_started = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def runner(*args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    two_started.set()
            release.wait(5)
            with lock:
                active -= 1
            return "Transcript"

        with (
            patch.object(api, "run_pipeline", side_effect=runner),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            try:
                for _ in range(3):
                    # These responses must return while both workers are blocked.
                    response = self.client.post(
                        "/jobs",
                        data={"file": SimpleUploadedFile("voice.wav", b"audio")},
                    )
                    self.assertEqual(response.status_code, 202)
                self.assertTrue(two_started.wait(5))
                self.assertEqual(self.client.get("/jobs").json()["active"], 2)
                self.assertEqual(self.client.get("/jobs").json()["queued"], 1)
                legacy = pool.submit(
                    self.client.post,
                    "/transcribe",
                    data={"file": SimpleUploadedFile("legacy.wav", b"audio")},
                )
                deadline = time.monotonic() + 3
                while (
                    self.client.get("/jobs").json()["queued"] < 2
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                self.assertEqual(self.client.get("/jobs").json()["queued"], 2)
                self.assertFalse(legacy.done())
            finally:
                release.set()
            self.assertEqual(legacy.result(timeout=5).status_code, 200)
        self.assertEqual(peak, 2)

    def test_full_queue_returns_429_without_saving_another_upload(self):
        self.login()
        queue = self.queue
        queue.stop()
        queue.max_pending = 2
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    "/jobs", data={"file": SimpleUploadedFile("voice.wav", b"audio")}
                ).status_code,
                202,
            )
        existing = set(queue.uploads.iterdir())
        response = self.client.post(
            "/jobs", data={"file": SimpleUploadedFile("extra.wav", b"audio")}
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(set(queue.uploads.iterdir()), existing)

    def test_upload_retry_key_returns_existing_job_without_running_twice(self):
        self.login()
        headers = {"Idempotency-Key": "same-recording-retry"}
        with patch.object(api, "run_pipeline", return_value="Transcript") as runner:
            first = self.client.post(
                "/jobs",
                headers=headers,
                data={"file": SimpleUploadedFile("voice.wav", b"audio")},
            )
            job_id = first.json()["id"]
            self.wait_for_job(job_id)
            second = self.client.post(
                "/jobs",
                headers=headers,
                data={"file": SimpleUploadedFile("voice.wav", b"audio")},
            )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["id"], job_id)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(self.client.get("/jobs").json()["jobs"]), 1)

    def test_concurrent_duplicate_uploads_create_only_one_job(self):
        self.login()
        queue = self.queue
        barrier = threading.Barrier(2)

        def simultaneous_lookup(*args):
            barrier.wait(timeout=5)

        def upload():
            return self.client.post(
                "/jobs",
                headers={"Idempotency-Key": "concurrent-retry"},
                data={"file": SimpleUploadedFile("voice.wav", b"audio")},
            )

        with (
            patch.object(queue, "find_request", side_effect=simultaneous_lookup),
            patch.object(api, "run_pipeline", return_value="Transcript") as runner,
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first, second = list(pool.map(lambda _: upload(), range(2)))
            job_id = first.json()["id"]
            self.wait_for_job(job_id)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["id"], job_id)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(self.client.get("/jobs").json()["jobs"]), 1)
        deadline = time.monotonic() + 2
        while list(queue.uploads.iterdir()) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(list(queue.uploads.iterdir()), [])

    def test_accepted_retry_key_still_works_when_queue_is_full(self):
        self.login()
        queue = self.queue
        queue.stop()
        queue.max_pending = 2
        first = self.client.post(
            "/jobs",
            headers={"Idempotency-Key": "one"},
            data={"file": SimpleUploadedFile("one.wav", b"audio")},
        )
        self.client.post(
            "/jobs",
            headers={"Idempotency-Key": "two"},
            data={"file": SimpleUploadedFile("two.wav", b"audio")},
        )
        repeated = self.client.post(
            "/jobs",
            headers={"Idempotency-Key": "one"},
            data={"file": SimpleUploadedFile("one.wav", b"audio")},
        )
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(first.json()["id"], repeated.json()["id"])
        self.assertEqual(len(list(queue.uploads.iterdir())), 2)

    def test_setup_preserves_existing_settings(self):
        from scripts import bootstrap_login as setup_login

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / ".env").write_text(
                "GEMINI_API_KEY=keep-me\nAPP_USERNAME=old\nAPP_PASSWORD_HASH=old\n"
            )
            with (
                patch.object(
                    setup_login,
                    "__file__",
                    str(path / "scripts" / "bootstrap_login.py"),
                ),
                patch("sys.argv", ["scripts.bootstrap_login", "--username", "tester"]),
                patch.object(
                    setup_login.getpass, "getpass", return_value=self.password
                ),
                patch("builtins.print"),
            ):
                setup_login.main()
            content = (path / ".env").read_text()
            self.assertIn("GEMINI_API_KEY=keep-me", content)
            self.assertIn("APP_USERNAME=tester", content)
            self.assertEqual(content.count("APP_PASSWORD_HASH="), 1)
            self.assertIn("APP_PASSWORD_HASH='pbkdf2_sha256$", content)
            self.assertNotIn(self.password, content)
            self.assertEqual((path / ".env").stat().st_mode & 0o777, 0o600)
