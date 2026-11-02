"""Admin probes verify audio routing without paid requests or private recordings."""

import base64
import json
import os
import re
import wave
from unittest.mock import MagicMock, patch

import httpx
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from google import genai
from google.genai import errors

from apps.transcriptions.admin import _connection_test_lock
from apps.transcriptions.models import ModelProvider
from apps.transcriptions.services import connection_test as probe
from apps.transcriptions.services import providers


class ConnectionTestTests(TestCase):
    def setUp(self):
        self.provider = ModelProvider.objects.create(
            name="Audio test",
            kind="openai",
            model="test-primary",
            fallback_model="must-not-run",
            base_url="https://audio.example.test/v1",
            is_enabled=False,
        )
        self.provider.set_api_key("fake-private-test-key")
        self.provider.save()

    def test_bundled_audio_is_short_non_silent_pcm(self):
        with wave.open(str(probe.TEST_AUDIO)) as audio:
            self.assertEqual(
                (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()),
                (1, 2, 16000),
            )
            self.assertTrue(1 < audio.getnframes() / audio.getframerate() < 5)
            self.assertGreater(len(set(audio.readframes(audio.getnframes()))), 10)
        self.assertLess(probe.TEST_AUDIO.stat().st_size, 200_000)

    def test_openai_uses_real_audio_adapter_with_no_fallback(self):
        real_client = httpx.Client
        calls = []

        def respond(request):
            self.assertEqual(
                str(request.url), "https://audio.example.test/v1/audio/transcriptions"
            )
            self.assertEqual(
                request.headers["Authorization"], "Bearer fake-private-test-key"
            )
            body = request.content
            self.assertIn(b"test-primary", body)
            self.assertNotIn(b"must-not-run", body)
            self.assertIn(probe.TEST_PROMPT.encode(), body)
            self.assertIn(b"RIFF", body)
            calls.append(request)
            return httpx.Response(
                200, json={"text": "Hello, this is a transcription test."}
            )

        def create_client(**options):
            self.assertEqual(options["timeout"].read, 15)
            self.assertFalse(options["follow_redirects"])
            return real_client(**options, transport=httpx.MockTransport(respond))

        with patch.object(providers.httpx, "Client", side_effect=create_client):
            result = probe.execute_connection_test(self.provider.pk)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("Hello", json.dumps(result))

    def test_custom_gemini_real_sdk_uses_inline_audio_and_own_key(self):
        self.provider.kind = "gemini"
        self.provider.base_url = "https://gemini.example.test"
        self.provider.save()
        real_client = genai.Client
        calls = []

        def respond(request):
            self.assertEqual(request.url.host, "gemini.example.test")
            self.assertEqual(
                request.url.path, "/v1beta/models/test-primary:generateContent"
            )
            self.assertEqual(request.headers["x-goog-api-key"], "fake-private-test-key")
            payload = json.loads(request.content)
            parts = payload["contents"][0]["parts"]
            inline = next(part["inlineData"] for part in parts if "inlineData" in part)
            self.assertTrue(base64.b64decode(inline["data"]).startswith(b"RIFF"))
            calls.append(request)
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "Test transcript"}]}}
                    ]
                },
            )

        def create_client(**options):
            http_options = options["http_options"]
            self.assertEqual(http_options.timeout, 15_000)
            self.assertEqual(http_options.retry_options.attempts, 1)
            http_options.httpx_client = httpx.Client(
                transport=httpx.MockTransport(respond)
            )
            return real_client(**options)

        with (
            patch.object(probe.genai, "Client", side_effect=create_client),
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake-env-key"}),
        ):
            result = probe.execute_connection_test(self.provider.pk)
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(calls), 1)

    def test_direct_gemini_keeps_files_adapter_and_uses_saved_key(self):
        self.provider.kind = "gemini"
        self.provider.base_url = ""
        self.provider.save()
        client = MagicMock()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake-env-key"}),
            patch.object(probe.genai, "Client", return_value=client) as create,
            patch.object(
                probe, "run_transcription_pipeline_raw", return_value="Test"
            ) as adapter,
            patch.object(probe, "transcribe_gemini_inline") as proxy,
        ):
            self.assertTrue(probe.execute_connection_test(self.provider.pk)["ok"])
        self.assertEqual(create.call_args.kwargs["api_key"], "fake-private-test-key")
        self.assertEqual(adapter.call_args.kwargs["fallback_model"], "")
        self.assertEqual(adapter.call_args.kwargs["input_path"], probe.TEST_AUDIO)
        proxy.assert_not_called()
        client.close.assert_called_once()

    def test_missing_gemini_credentials_never_call_provider(self):
        self.provider.kind = "gemini"
        self.provider.set_api_key("")
        self.provider.save()
        with (
            patch.object(probe.genai, "Client") as client,
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake-env-key"}),
        ):
            result = probe.execute_connection_test(self.provider.pk)
            self.assertFalse(result["ok"])
            self.assertIn("API key", result["message"])
            self.provider.base_url = ""
            self.provider.save()
            result = probe.execute_connection_test(self.provider.pk)
            self.assertIn("API key", result["message"])
        client.assert_not_called()

    def test_errors_are_useful_and_never_include_provider_bodies_or_credentials(self):
        for code, expected in (
            (400, "rejected"),
            (401, "Authentication"),
            (402, "credits"),
            (403, "Access denied"),
            (404, "not found"),
            (429, "quota"),
            (503, "temporarily"),
        ):
            with self.subTest(code=code):
                with patch.object(
                    probe,
                    "transcribe_openai",
                    side_effect=providers.ProviderError(
                        f"Provider returned HTTP {code}. Check its configuration."
                    ),
                ):
                    result = probe.execute_connection_test(self.provider.pk)
                self.assertFalse(result["ok"])
                self.assertIn(expected, result["message"])
        cases = [
            (httpx.ReadTimeout("fake-private-test-key"), "timed out"),
            (httpx.ConnectError("fake-private-test-key"), "connect"),
            (
                providers.ProviderError("Provider returned invalid JSON."),
                "invalid or empty",
            ),
            (RuntimeError("fake-private-test-key"), "failed"),
        ]
        for exception, expected in cases:
            with self.subTest(exception=type(exception).__name__):
                with patch.object(probe, "transcribe_openai", side_effect=exception):
                    result = probe.execute_connection_test(self.provider.pk)
                self.assertIn(expected, result["message"])
                self.assertNotIn("fake-private", json.dumps(result))
        self.provider.kind = "gemini"
        self.provider.save()
        with patch.object(
            probe.genai,
            "Client",
            side_effect=errors.ClientError(
                401, {"error": {"message": "fake-private-test-key"}}
            ),
        ):
            result = probe.execute_connection_test(self.provider.pk)
        self.assertIn("Authentication", result["message"])
        self.assertNotIn("fake-private", json.dumps(result))

    def test_empty_response_fails_without_fallback(self):
        with patch.object(probe, "transcribe_openai", return_value=" ") as adapter:
            result = probe.execute_connection_test(self.provider.pk)
        self.assertFalse(result["ok"])
        self.assertEqual(adapter.call_count, 1)
        self.assertEqual(adapter.call_args.kwargs["fallback_model"], "")

    def test_parent_uses_only_id_and_enforces_deadline(self):
        with patch.object(
            probe,
            "run_isolated",
            return_value=json.dumps({"ok": True, "message": "Connected"}),
        ) as runner:
            result = probe.check_connection(self.provider)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["elapsed_seconds"], 0)
        runner.assert_called_once_with(
            {"connection_test_provider": str(self.provider.pk)}, timeout_seconds=30
        )
        for error in (TimeoutError("fake-private"), RuntimeError("fake-private")):
            with patch.object(probe, "run_isolated", side_effect=error):
                result = probe.check_connection(self.provider)
            self.assertFalse(result["ok"])
            self.assertNotIn("fake-private", json.dumps(result))

    def test_admin_permissions_csrf_post_and_saved_settings(self):
        url = f"/admin/web/modelprovider/{self.provider.pk}/test-connection/"
        edit = f"/admin/web/modelprovider/{self.provider.pk}/change/"
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(url).status_code, 302)
        self.assertEqual(client.post(url).status_code, 403)
        admin = get_user_model().objects.create_superuser(
            "test-admin", password="a-test-password-1234"
        )
        staff = get_user_model().objects.create_user("test-staff", is_staff=True)
        client.force_login(staff)
        with patch("apps.transcriptions.admin.check_connection") as check:
            self.assertEqual(client.post(url).status_code, 403)
            client.force_login(admin)
            self.assertEqual(client.get(url).status_code, 405)
            self.assertEqual(client.post(url).status_code, 403)
            check.assert_not_called()
            body = client.get(edit).content.decode()
            token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)[1]
            self.assertIn('id="provider-test-button"', body)
            self.assertNotIn("fake-private", body)
            self.assertNotIn(
                'id="provider-test-button"',
                client.get("/admin/web/modelprovider/add/").content.decode(),
            )
            check.return_value = {
                "ok": True,
                "message": "Connected",
                "elapsed_seconds": 0.2,
            }
            response = client.post(
                url,
                {
                    "csrfmiddlewaretoken": token,
                    "model": "unsaved-model",
                    "api_key": "unsaved-key",
                },
                HTTP_ACCEPT="application/json",
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(check.call_args.args[0].model, "test-primary")
            self.assertEqual(
                check.call_args.args[0].get_api_key(), "fake-private-test-key"
            )
            check.reset_mock()
            _connection_test_lock.acquire()
            try:
                self.assertEqual(
                    client.post(
                        url,
                        {"csrfmiddlewaretoken": token},
                        HTTP_ACCEPT="application/json",
                    ).status_code,
                    429,
                )
                check.assert_not_called()
            finally:
                _connection_test_lock.release()
            self.assertEqual(
                client.post(
                    "/admin/web/modelprovider/999999/test-connection/",
                    {"csrfmiddlewaretoken": token},
                ).status_code,
                404,
            )
            response = client.post(url, {"csrfmiddlewaretoken": token})
            self.assertRedirects(response, edit)
            check.reset_mock()
            client.force_login(staff)
            self.assertEqual(
                client.post(
                    url, {"csrfmiddlewaretoken": token}, HTTP_ACCEPT="application/json"
                ).status_code,
                403,
            )
            check.assert_not_called()
        self.provider.refresh_from_db()
        self.assertFalse(self.provider.is_default)
        self.assertFalse(self.provider.is_enabled)
        self.assertEqual(self.provider.model, "test-primary")
