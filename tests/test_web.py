import os
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

import auth
import transcription_api as api


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "a-test-password-12345"
        cls.password_hash = auth.hash_password(cls.password)

    def setUp(self):
        self.env = patch.dict(os.environ, {
            "APP_USERNAME": "tester", "APP_PASSWORD_HASH": self.password_hash,
            "SECURE_COOKIES": "false", "MAX_UPLOAD_MB": "1",
        })
        self.env.start()
        self.data_dir = tempfile.TemporaryDirectory()
        self.jobs_env = patch.dict(os.environ, {"JOB_DATA_DIR": self.data_dir.name, "TRANSCRIPTION_WORKERS": "2", "MAX_PENDING_JOBS": "20"})
        self.jobs_env.start()
        auth._sessions.clear()
        auth._attempts.clear()
        self.client = TestClient(api.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.jobs_env.stop()
        self.data_dir.cleanup()
        self.env.stop()

    def login(self):
        return self.client.post("/auth/login", json={"username": "tester", "password": self.password})

    def test_public_frontend_and_assets(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        for path in ("/static/app.js", "/static/styles.css", "/health"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertIn("frame-ancestors 'none'", self.client.get("/").headers["content-security-policy"])

    def test_anonymous_requests_are_blocked_before_pipeline(self):
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(self.client.get("/transcription/defaults").status_code, 401)
        for path in ("/jobs", "/jobs/missing", "/jobs/missing/result"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/jobs", content=b"invalid multipart").status_code, 401)
        self.assertEqual(self.client.post("/jobs/missing/retry").status_code, 401)
        self.assertEqual(self.client.delete("/jobs/missing").status_code, 401)
        with patch.object(api, "_run_pipeline") as pipeline:
            self.assertEqual(self.client.post("/transcribe", content=b"invalid multipart").status_code, 401)
            self.assertEqual(self.client.post("/transcribe/", files={"file": ("voice.wav", b"audio")}).status_code, 401)
            pipeline.assert_not_called()

    def test_wrong_password_and_username(self):
        for username, password in (("tester", "wrong"), ("wrong", self.password)):
            response = self.client.post("/auth/login", json={"username": username, "password": password})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn(auth.COOKIE_NAME, response.cookies)

    def test_session_logout_and_revocation(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        token = self.client.cookies.get(auth.COOKIE_NAME)
        self.assertEqual(self.client.get("/auth/me").json(), {"username": "tester", "max_upload_mb": 1})
        self.assertEqual(self.client.post("/auth/logout").status_code, 204)
        self.client.cookies.set(auth.COOKIE_NAME, token)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_expired_and_forged_sessions(self):
        self.client.cookies.set(auth.COOKIE_NAME, "forged")
        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        auth._sessions["expired"] = time.monotonic() - 1
        self.client.cookies.set(auth.COOKIE_NAME, "expired")
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_secure_cookie_setting(self):
        with patch.dict(os.environ, {"SECURE_COOKIES": "true"}):
            self.assertIn("Secure", self.login().headers["set-cookie"])

    def test_cross_site_writes_rejected(self):
        response = self.client.post("/auth/login", headers={"Origin": "https://other.example"}, json={"username": "tester", "password": self.password})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/auth/logout", headers={"Origin": "null"}).status_code, 403)
        self.assertEqual(self.client.post("/auth/logout", headers={"Origin": "http://testserver"}).status_code, 204)

    def test_rate_limit(self):
        for _ in range(5):
            self.client.post("/auth/login", json={"username": "tester", "password": "wrong"})
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    def test_unconfigured_login_fails_closed(self):
        with patch.dict(os.environ, {"APP_PASSWORD_HASH": ""}):
            self.assertEqual(self.login().status_code, 503)

    def test_authenticated_upload_language_and_cleanup(self):
        self.login()
        seen_paths = []

        def fake_pipeline(path, model, chunk_minutes, language, fallback, prompt):
            self.assertEqual(path.read_bytes(), b"audio-content")
            self.assertEqual(language, "fa")
            seen_paths.append(path)
            return "SPEAKER_1: سلام دنیا"

        with patch.object(api, "_run_pipeline", side_effect=fake_pipeline):
            response = self.client.post("/transcribe", files={"file": ("recording.wav", b"audio-content")}, data={"language_hint": "fa"})
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
        body_ref = schema["paths"]["/transcribe"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        fields = schema["components"]["schemas"][body_ref.rsplit("/", 1)[-1]]["properties"]
        self.assertEqual(set(defaults), set(fields) - {"file"})
        for name, value in defaults.items():
            self.assertEqual(value, fields[name]["default"])

    def test_custom_settings_reach_pipeline(self):
        self.login()
        settings = {
            "model": "custom-primary", "fallback_model": "custom-fallback",
            "chunk_minutes": "7.5", "language_hint": "Italian",
            "prompt": "Transcribe verbatim.\nKeep speaker names.",
        }
        with patch.object(api, "_run_pipeline", return_value="Custom transcript") as pipeline:
            response = self.client.post("/transcribe", files={"file": ("voice.wav", b"audio")}, data=settings)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pipeline.call_args.args[1:], ("custom-primary", 7.5, "Italian", "custom-fallback", settings["prompt"]))

    def test_disabled_chunking_fallback_and_auto_language(self):
        self.login()
        with patch.object(api, "_run_pipeline", return_value="Transcript") as pipeline:
            response = self.client.post("/transcribe", files={"file": ("voice.wav", b"audio")}, data={"chunk_minutes": "0", "fallback_model": "", "language_hint": ""})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pipeline.call_args.args[2:5], (0, "", ""))

    def test_invalid_uploads_do_not_run_pipeline(self):
        self.login()
        with patch.object(api, "_run_pipeline") as pipeline:
            self.assertEqual(self.client.post("/transcribe", files={"file": ("empty.wav", b"")}).status_code, 400)
            self.assertEqual(self.client.post("/transcribe", files={"file": ("big.wav", b"a" * (1024 * 1024 + 1))}).status_code, 413)
            self.assertEqual(self.client.post("/transcribe", files={"file": ("voice.wav", b"audio")}, data={"chunk_minutes": "-1"}).status_code, 422)
            pipeline.assert_not_called()

    def test_pipeline_error_does_not_expose_internal_details(self):
        self.login()
        with patch.object(api, "_run_pipeline", side_effect=RuntimeError("private-key-or-server-detail")), patch("job_queue.logger.exception"):
            response = self.client.post("/transcribe", files={"file": ("voice.wav", b"audio")})
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
        with patch.object(api, "_run_pipeline", return_value="SPEAKER_1: Background result"):
            response = self.client.post("/jobs", files={"file": ("voice.wav", b"audio")})
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
        with patch.object(api, "_run_pipeline", return_value="Private result"):
            job_id = self.client.post("/jobs", files={"file": ("voice.wav", b"audio")}).json()["id"]
            self.wait_for_job(job_id)
        with patch.dict(os.environ, {"APP_USERNAME": "another-account"}):
            self.assertEqual(self.client.get("/jobs").json()["jobs"], [])
            self.assertEqual(self.client.get(f"/jobs/{job_id}").status_code, 404)
            self.assertEqual(self.client.get(f"/jobs/{job_id}/result").status_code, 404)
            self.assertEqual(self.client.post(f"/jobs/{job_id}/retry").status_code, 404)
            self.assertEqual(self.client.delete(f"/jobs/{job_id}").status_code, 404)

    def test_failed_job_can_retry_without_uploading_again(self):
        self.login()
        with patch.object(api, "_run_pipeline", side_effect=RuntimeError("private provider error")), patch("job_queue.logger.exception"):
            job_id = self.client.post("/jobs", files={"file": ("voice.wav", b"audio")}).json()["id"]
            failed = self.wait_for_job(job_id)
        self.assertTrue(failed["retryable"])
        self.assertNotIn("private provider error", str(failed))
        self.assertEqual(self.client.get(f"/jobs/{job_id}/result").status_code, 409)
        with patch.object(api, "_run_pipeline", return_value="Retry result"):
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

        with patch.object(api, "_run_pipeline", side_effect=runner), ThreadPoolExecutor(max_workers=1) as pool:
            try:
                for _ in range(3):
                    # These responses must return while both workers are blocked.
                    response = self.client.post("/jobs", files={"file": ("voice.wav", b"audio")})
                    self.assertEqual(response.status_code, 202)
                self.assertTrue(two_started.wait(5))
                self.assertEqual(self.client.get("/jobs").json()["active"], 2)
                self.assertEqual(self.client.get("/jobs").json()["queued"], 1)
                legacy = pool.submit(self.client.post, "/transcribe", files={"file": ("legacy.wav", b"audio")})
                deadline = time.monotonic() + 3
                while self.client.get("/jobs").json()["queued"] < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(self.client.get("/jobs").json()["queued"], 2)
                self.assertFalse(legacy.done())
            finally:
                release.set()
            self.assertEqual(legacy.result(timeout=5).status_code, 200)
        self.assertEqual(peak, 2)

    def test_full_queue_returns_429_without_saving_another_upload(self):
        self.login()
        queue = self.client.app.state.jobs
        queue.stop()
        queue.max_pending = 2
        for _ in range(2):
            self.assertEqual(self.client.post("/jobs", files={"file": ("voice.wav", b"audio")}).status_code, 202)
        existing = set(queue.uploads.iterdir())
        response = self.client.post("/jobs", files={"file": ("extra.wav", b"audio")})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")
        self.assertEqual(set(queue.uploads.iterdir()), existing)

    def test_upload_retry_key_returns_existing_job_without_running_twice(self):
        self.login()
        headers = {"Idempotency-Key": "same-recording-retry"}
        with patch.object(api, "_run_pipeline", return_value="Transcript") as runner:
            first = self.client.post("/jobs", files={"file": ("voice.wav", b"audio")}, headers=headers)
            job_id = first.json()["id"]
            self.wait_for_job(job_id)
            second = self.client.post("/jobs", files={"file": ("voice.wav", b"audio")}, headers=headers)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(second.json()["id"], job_id)
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(self.client.get("/jobs").json()["jobs"]), 1)

    def test_concurrent_duplicate_uploads_create_only_one_job(self):
        self.login()
        queue = self.client.app.state.jobs
        barrier = threading.Barrier(2)

        def simultaneous_lookup(*args):
            barrier.wait(timeout=5)
            return None

        def upload():
            return self.client.post("/jobs", files={"file": ("voice.wav", b"audio")}, headers={"Idempotency-Key": "concurrent-retry"})

        with patch.object(queue, "find_request", side_effect=simultaneous_lookup), patch.object(api, "_run_pipeline", return_value="Transcript") as runner, ThreadPoolExecutor(max_workers=2) as pool:
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
        queue = self.client.app.state.jobs
        queue.stop()
        queue.max_pending = 2
        first = self.client.post("/jobs", files={"file": ("one.wav", b"audio")}, headers={"Idempotency-Key": "one"})
        self.client.post("/jobs", files={"file": ("two.wav", b"audio")}, headers={"Idempotency-Key": "two"})
        repeated = self.client.post("/jobs", files={"file": ("one.wav", b"audio")}, headers={"Idempotency-Key": "one"})
        self.assertEqual(repeated.status_code, 202)
        self.assertEqual(first.json()["id"], repeated.json()["id"])
        self.assertEqual(len(list(queue.uploads.iterdir())), 2)

    def test_setup_preserves_existing_settings(self):
        import setup_login

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / ".env").write_text("GEMINI_API_KEY=keep-me\nAPP_USERNAME=old\nAPP_PASSWORD_HASH=old\n")
            with patch.object(setup_login, "__file__", str(path / "setup_login.py")), patch("sys.argv", ["setup_login.py", "--username", "tester"]), patch.object(setup_login.getpass, "getpass", return_value=self.password), patch("builtins.print"):
                setup_login.main()
            content = (path / ".env").read_text()
            self.assertIn("GEMINI_API_KEY=keep-me", content)
            self.assertIn("APP_USERNAME=tester", content)
            self.assertEqual(content.count("APP_PASSWORD_HASH="), 1)
            self.assertIn("APP_PASSWORD_HASH='pbkdf2_sha256$", content)
            self.assertNotIn(self.password, content)
            self.assertEqual((path / ".env").stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
