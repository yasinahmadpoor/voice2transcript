import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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
        auth._sessions.clear()
        auth._attempts.clear()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.client.close()
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
        self.assertFalse(seen_paths[0].parent.exists())

    def test_invalid_uploads_do_not_run_pipeline(self):
        self.login()
        with patch.object(api, "_run_pipeline") as pipeline:
            self.assertEqual(self.client.post("/transcribe", files={"file": ("empty.wav", b"")}).status_code, 400)
            self.assertEqual(self.client.post("/transcribe", files={"file": ("big.wav", b"a" * (1024 * 1024 + 1))}).status_code, 413)
            self.assertEqual(self.client.post("/transcribe", files={"file": ("voice.wav", b"audio")}, data={"chunk_minutes": "-1"}).status_code, 422)
            pipeline.assert_not_called()

    def test_pipeline_error_does_not_expose_internal_details(self):
        self.login()
        with patch.object(api, "_run_pipeline", side_effect=RuntimeError("private-key-or-server-detail")), patch.object(api.logger, "exception"):
            response = self.client.post("/transcribe", files={"file": ("voice.wav", b"audio")})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-key-or-server-detail", response.text)

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
