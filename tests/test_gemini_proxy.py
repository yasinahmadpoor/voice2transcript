"""Exercise custom Gemini routing through the real SDK with mocked HTTP."""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from google import genai

from apps.transcriptions.forms import ModelProviderAdminForm
from apps.transcriptions.models import ModelProvider
from apps.transcriptions.services import jobs, providers


class GeminiProxyTests(TestCase):
    def setUp(self):
        self.provider = ModelProvider(
            name="Metis Gemini",
            kind="gemini",
            base_url="https://api.metisai.ir",
            model="gemini-test",
        )
        self.provider.set_api_key("fake-metis-key")
        self.provider.save()
        self.data = {
            "name": "Metis Gemini",
            "kind": "gemini",
            "base_url": self.provider.base_url,
            "model": "gemini-test",
            "is_enabled": True,
        }

    def test_custom_url_validation_and_explicit_credentials(self):
        self.provider.full_clean()
        for url in (
            "file:///tmp/audio",
            "https://key:secret@proxy.example.test",
            "https://proxy.example.test?key=secret",
            "https://[broken",
        ):
            self.provider.base_url = url
            with self.subTest(url=url), self.assertRaises(ValidationError):
                self.provider.full_clean()
        form = ModelProviderAdminForm(self.data, instance=self.provider)
        self.assertTrue(form.is_valid(), form.errors)
        form = ModelProviderAdminForm(
            {**self.data, "clear_api_key": True}, instance=self.provider
        )
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)
        form = ModelProviderAdminForm({**self.data, "name": "New proxy"})
        self.assertFalse(form.is_valid())
        form = ModelProviderAdminForm(
            {**self.data, "name": "New proxy", "api_key": "new-fake-key"}
        )
        self.assertTrue(form.is_valid(), form.errors)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_admin_can_save_a_gemini_proxy(self):
        user = get_user_model().objects.create_superuser(
            "proxy-admin", password="a-test-only-password"
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            "/admin/web/modelprovider/add/",
            {
                **self.data,
                "name": "Added in admin",
                "api_key": "admin-fake-metis-key",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        created = ModelProvider.objects.get(name="Added in admin")
        self.assertEqual(created.base_url, "https://api.metisai.ir")
        self.assertEqual(created.get_api_key(), "admin-fake-metis-key")
        self.assertNotIn(
            b"admin-fake-metis-key",
            client.get(f"/admin/web/modelprovider/{created.pk}/change/").content,
        )

    def test_changing_gemini_host_requires_a_new_explicit_key(self):
        changed = {**self.data, "base_url": "https://another-proxy.example.test"}
        form = ModelProviderAdminForm(changed, instance=self.provider)
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)
        self.provider.refresh_from_db()
        form = ModelProviderAdminForm(
            {**changed, "api_key": "fake-new-host-key"}, instance=self.provider
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.get_api_key(), "fake-new-host-key")

    def test_custom_url_never_falls_back_to_google_environment_key(self):
        self.provider.set_api_key("")
        self.provider.save()
        with (
            patch.object(jobs, "get_api_key_from_env") as environment_key,
            patch.object(jobs.genai, "Client") as client,
        ):
            with self.assertRaisesRegex(providers.ProviderError, "API key"):
                jobs.run_pipeline(
                    Path("fake"),
                    "gemini-test",
                    20,
                    "fa",
                    "",
                    "Prompt",
                    provider=str(self.provider.pk),
                )
            environment_key.assert_not_called()
            client.assert_not_called()

    def proxy_audio(self, response_text, chunks=1, minutes=20):
        real_client = genai.Client
        requests = []

        def transport(request):
            self.assertEqual(request.url.host, "api.metisai.ir")
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.headers["x-goog-api-key"], "fake-metis-key")
            self.assertTrue(request.url.path.startswith("/v1beta/models/"))
            self.assertTrue(request.url.path.endswith(":generateContent"))
            payload = json.loads(request.content)
            parts = payload["contents"][0]["parts"]
            audio = next(part["inlineData"] for part in parts if "inlineData" in part)
            self.assertEqual(audio.get("mimeType", audio.get("mime_type")), "audio/wav")
            self.assertEqual(base64.b64decode(audio["data"]), b"synthetic audio")
            self.assertFalse(any("fileData" in part for part in parts))
            text = next(part["text"] for part in parts if "text" in part)
            self.assertIn("Preferred spoken language hint: fa", text)
            requests.append(request)
            value = response_text(request, len(requests))
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"role": "model", "parts": [{"text": value}]}}
                    ]
                },
            )

        def client(**kwargs):
            options = kwargs["http_options"]
            self.assertEqual(str(options.base_url), "https://api.metisai.ir")
            options.httpx_client = httpx.Client(
                transport=httpx.MockTransport(transport)
            )
            return real_client(**kwargs)

        with (
            patch.object(jobs.genai, "Client", side_effect=client),
            patch.object(
                providers,
                "convert_to_wav",
                side_effect=lambda source, dest: dest.write_bytes(b"synthetic audio"),
            ),
            patch.object(
                providers,
                "split_wav",
                side_effect=lambda path, length: [
                    (path, index * 300) for index in range(chunks)
                ],
            ) as split,
        ):
            result = jobs.run_pipeline(
                Path("fake"),
                "gemini-test",
                minutes,
                "fa",
                "gemini-fallback",
                "Transcribe exactly",
                provider=str(self.provider.pk),
            )
            self.assertEqual(
                split.call_args.args[1], min(minutes, 5) if minutes > 0 else 0
            )
        return result, requests

    def test_real_sdk_uses_custom_url_inline_audio_and_preserves_chunk_order(self):
        result, requests = self.proxy_audio(
            lambda request, count: f"SPEAKER_1: chunk {count}", chunks=2
        )
        self.assertEqual(result, "SPEAKER_1: chunk 1\n\nSPEAKER_1: chunk 2")
        self.assertEqual(len(requests), 2)

    def test_inline_fallback_after_empty_primary_and_no_splitting(self):
        result, requests = self.proxy_audio(
            lambda request, count: ""
            if "gemini-test:" in request.url.path
            else "Fallback transcript",
            minutes=0,
        )
        self.assertEqual(result, "Fallback transcript")
        self.assertEqual(len(requests), 3)

    def test_inline_request_limits_are_checked_before_provider_calls(self):
        for size, prompt, expected in (
            (10 * 1024 * 1024 + 1, "Prompt", "10 MB"),
            (10 * 1024 * 1024, "\x00" * (2 * 1024 * 1024), "inline request limit"),
        ):

            def convert(source, dest):
                with dest.open("wb") as file:
                    file.truncate(size)

            client = MagicMock()
            with (
                self.subTest(size=size),
                patch.object(providers, "convert_to_wav", side_effect=convert),
                patch.object(
                    providers,
                    "split_wav",
                    side_effect=lambda path, minutes: [(path, 0)],
                ),
            ):
                with self.assertRaisesRegex(providers.ProviderError, expected):
                    providers.transcribe_gemini_inline(
                        client, Path("fake"), "gemini-test", 0, "", "", prompt
                    )
            client.models.generate_content.assert_not_called()
