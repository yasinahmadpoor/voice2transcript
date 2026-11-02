"""Provider administration, secret handling, and audio routing without paid calls."""

import json
import re
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import httpx
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, TransactionTestCase, override_settings

from apps.transcriptions.defaults import transcription_defaults
from apps.transcriptions.forms import ModelProviderAdminForm, TranscriptionForm
from apps.transcriptions.models import ModelProvider
from apps.transcriptions.services import jobs, providers, runtime
from apps.transcriptions.services.queue import JobQueue
from common.http import APIError


class ProviderTests(TestCase):
    def setUp(self):
        self.provider = ModelProvider.objects.create(
            name="Custom audio",
            kind="openai",
            base_url="https://audio.example.test/v1",
            model="audio-model",
            fallback_model="audio-fallback",
        )
        self.provider.set_api_key("fake-private-provider-key")
        self.provider.save()

    def test_encryption_and_url_validation(self):
        self.provider.refresh_from_db()
        self.assertNotIn("fake-private-provider-key", self.provider.api_key_encrypted)
        self.assertEqual(self.provider.get_api_key(), "fake-private-provider-key")
        with override_settings(SECRET_KEY="a-different-signing-key"):
            with self.assertRaisesRegex(RuntimeError, "cannot be decrypted"):
                self.provider.get_api_key()
        for value in (
            "",
            "file:///tmp/test",
            "https://user:pass@audio.example.test/v1",
            "https://audio.example.test/?key=secret",
        ):
            self.provider.base_url = value
            with self.subTest(url=value), self.assertRaises(ValidationError):
                self.provider.full_clean()

    def test_default_selection_and_disabled_provider(self):
        self.provider.make_default()
        self.assertEqual(transcription_defaults()["provider"], str(self.provider.pk))
        other = ModelProvider.objects.create(
            name="Other", kind="gemini", model="gemini-model"
        )
        other.make_default()
        self.provider.refresh_from_db()
        self.assertFalse(self.provider.is_default)
        self.assertEqual(ModelProvider.objects.filter(is_default=True).count(), 1)
        self.assertEqual(transcription_defaults()["model"], "gemini-model")
        self.provider.is_enabled = False
        self.provider.save()
        with self.assertRaises(APIError):
            providers.resolve_provider(str(self.provider.pk))
        with self.assertRaises(ValidationError):
            self.provider.make_default()
        with self.assertRaises(APIError):
            transcription_defaults("env")  # No seeded provider in this test DB.

    def test_invalid_provider_and_api_key_are_rejected(self):
        for value in ("missing", "999999", "https://untrusted.example.test", ""):
            with self.subTest(value=value), self.assertRaises(APIError):
                TranscriptionForm({"provider": value}, {})
        form = ModelProviderAdminForm(
            {
                "name": "Bad key",
                "kind": "gemini",
                "model": "gemini-model",
                "is_enabled": True,
                "api_key": "fake\nkey",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("api_key", form.errors)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_admin_can_create_edit_and_clear_keys_without_displaying_them(self):
        admin = get_user_model().objects.create_superuser(
            "provider-admin", password="a-test-password-1234"
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(admin)
        url = "/admin/web/modelprovider/add/"
        body = client.get(url).content.decode()
        self.assertNotIn('name="response_format"', body)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)[1]
        data = {
            "csrfmiddlewaretoken": token,
            "name": "Admin provider",
            "kind": "gemini",
            "base_url": "",
            "api_key": "admin-fake-secret",
            "model": "gemini-custom",
            "fallback_model": "",
            "is_enabled": "on",
            "_save": "Save",
        }
        response = client.post(url, data)
        self.assertEqual(response.status_code, 302)
        provider = ModelProvider.objects.get(name="Admin provider")
        self.assertEqual(provider.get_api_key(), "admin-fake-secret")
        self.assertTrue(provider.is_default)
        action = {
            "csrfmiddlewaretoken": token,
            "action": "use_as_default",
            "_selected_action": str(self.provider.pk),
        }
        self.assertEqual(
            client.post("/admin/web/modelprovider/", action).status_code, 302
        )
        self.assertTrue(ModelProvider.objects.get(pk=self.provider.pk).is_default)
        edit = f"/admin/web/modelprovider/{provider.pk}/change/"
        self.assertEqual(
            client.get(f"/admin/web/modelprovider/{provider.pk}/delete/").status_code,
            403,
        )
        self.assertNotIn(b"admin-fake-secret", client.get(edit).content)
        self.assertNotIn(b'name="response_format"', client.get(edit).content)
        data["api_key"] = ""
        data["model"] = "updated-model"
        self.assertEqual(client.post(edit, data).status_code, 302)
        provider.refresh_from_db()
        self.assertEqual(provider.get_api_key(), "admin-fake-secret")
        data["clear_api_key"] = "on"
        self.assertEqual(client.post(edit, data).status_code, 302)
        provider.refresh_from_db()
        self.assertEqual(provider.get_api_key(), "")
        self.assertNotIn("admin-fake-secret", str(list(LogEntry.objects.values())))
        staff = get_user_model().objects.create_user("staff", is_staff=True)
        staff.user_permissions.set(
            Permission.objects.filter(content_type__model="modelprovider")
        )
        client.force_login(staff)
        self.assertEqual(client.get(edit).status_code, 403)
        self.assertEqual(client.post(url, data).status_code, 403)

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_catalog_and_defaults_never_expose_secrets_or_urls(self):
        self.provider.make_default()
        client = Client()
        self.assertEqual(client.get("/transcription/providers").status_code, 401)
        user = get_user_model().objects.create_user("listener")
        client.force_login(user)
        response = client.get("/transcription/providers")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["providers"][0]["id"], str(self.provider.pk))
        for secret in (
            "fake-private-provider-key",
            self.provider.api_key_encrypted,
            "audio.example.test",
        ):
            self.assertNotIn(secret, response.content.decode())
            self.assertNotIn(secret, client.get("/openapi.json").content.decode())
        self.assertEqual(
            client.get("/transcription/defaults").json()["provider"],
            str(self.provider.pk),
        )

    def test_submission_and_retry_pin_provider_without_storing_credentials(self):
        self.provider.make_default()
        form = TranscriptionForm({}, {"file": SimpleUploadedFile("voice.wav", b"fake")})
        self.assertTrue(form.is_valid(), form.errors)
        parameters = dict(form.cleaned_data)
        upload = parameters.pop("file")
        calls = []

        def runner(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("Synthetic failure")
            return "Transcript"

        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory), runner)
            job = jobs.submit_recording(queue, "user:1", upload, parameters)
            with queue._db() as connection:
                stored = connection.execute(
                    "SELECT settings FROM jobs WHERE id=?", (job["id"],)
                ).fetchone()[0]
            self.assertEqual(json.loads(stored)["provider"], str(self.provider.pk))
            self.assertNotIn("fake-private-provider-key", stored)
            other = ModelProvider.objects.create(
                name="Replacement", kind="gemini", model="another"
            )
            other.make_default()

            def wait(status):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if queue.get(job["id"], "user:1")["status"] == status:
                        return
                    time.sleep(0.02)
                self.fail("Queue did not reach expected state")

            try:
                with patch("apps.transcriptions.services.queue.logger.exception"):
                    queue.start()
                    wait("failed")
                    queue.retry(job["id"], "user:1")
                    wait("completed")
                self.assertEqual(calls, [{"provider": str(self.provider.pk)}] * 2)
            finally:
                queue.stop()

    def test_gemini_uses_admin_key_and_redacts_sdk_errors(self):
        self.provider.kind = "gemini"
        self.provider.base_url = ""
        self.provider.save()
        with (
            patch.object(jobs.genai, "Client") as client,
            patch.object(
                jobs, "run_transcription_pipeline_raw", return_value="Transcript"
            ),
        ):
            self.assertEqual(
                jobs.execute_pipeline(
                    Path("fake"),
                    "model",
                    20,
                    "fa",
                    "",
                    "Prompt",
                    provider=str(self.provider.pk),
                ),
                "Transcript",
            )
            client.assert_called_once_with(api_key="fake-private-provider-key")
        with patch.object(
            jobs.genai, "Client", side_effect=ValueError("fake-private-provider-key")
        ):
            try:
                jobs.execute_pipeline(
                    Path("fake"),
                    "model",
                    20,
                    "fa",
                    "",
                    "Prompt",
                    provider=str(self.provider.pk),
                )
            except providers.ProviderError as error:
                self.assertNotIn(
                    "fake-private-provider-key",
                    "".join(traceback.format_exception(error)),
                )
            else:
                self.fail("Provider error was not raised")

    def request_audio(self, response_factory):
        requests = []
        original_client = httpx.Client

        def handle(request):
            requests.append(request)
            self.assertEqual(
                str(request.url), "https://audio.example.test/v1/audio/transcriptions"
            )
            self.assertEqual(
                request.headers["authorization"], "Bearer fake-private-provider-key"
            )
            self.assertIn(b'name="file"', request.read())
            self.assertIn(b'name="response_format"\r\n\r\njson\r\n', request.read())
            self.assertIn(b'name="prompt"\r\n\r\nVerbatim\r\n', request.read())
            return response_factory(request)

        def client(**kwargs):
            self.assertFalse(kwargs["follow_redirects"])
            return original_client(transport=httpx.MockTransport(handle), **kwargs)

        with (
            patch.object(providers.httpx, "Client", side_effect=client),
            patch.object(
                providers,
                "convert_to_wav",
                side_effect=lambda source, dest: dest.write_bytes(b"fake wav"),
            ),
            patch.object(
                providers, "split_wav", side_effect=lambda path, minutes: [(path, 0)]
            ) as split,
        ):
            text = providers.transcribe_openai(
                self.provider,
                Path("fake"),
                "audio-model",
                20,
                "fa",
                "audio-fallback",
                "Verbatim",
            )
            self.assertEqual(split.call_args.args[1], 10)
        return text, requests

    def test_compatible_multipart_and_empty_fallback(self):
        def respond(request):
            body = request.read()
            self.assertIn(b'name="language"', body)
            return httpx.Response(
                200,
                json={"text": "" if b"audio-model" in body else "Fallback transcript"},
            )

        text, requests = self.request_audio(respond)
        self.assertEqual(text, "Fallback transcript")
        self.assertEqual(len(requests), 2)

    def test_compatible_extracts_text_without_reformatting_its_content(self):
        transcript = '{"transcript":"Hello","language":"en"}'
        self.assertEqual(
            self.request_audio(
                lambda request: httpx.Response(200, json={"text": transcript})
            )[0],
            transcript,
        )

    def test_compatible_rejects_invalid_json_response(self):
        with self.assertRaisesRegex(providers.ProviderError, "invalid JSON"):
            self.request_audio(
                lambda request: httpx.Response(200, text="Plain transcript")
            )

    def test_compatible_errors_do_not_include_provider_response(self):
        for status in (302, 401, 429, 500):
            with (
                self.subTest(status=status),
                self.assertRaises(providers.ProviderError) as error,
            ):
                self.request_audio(
                    lambda request: httpx.Response(
                        status,
                        text="fake-private-provider-key",
                        headers={"Location": "https://another.example.test"},
                    )
                )
            self.assertNotIn("fake-private-provider-key", str(error.exception))


class ProviderQueueIntegrationTests(TransactionTestCase):
    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_admin_provider_executes_through_real_background_queue(self):
        user = get_user_model().objects.create_user("provider-listener")
        provider = ModelProvider(
            name="Queue Gemini", kind="gemini", model="gemini-test"
        )
        provider.set_api_key("fake-queue-provider-key")
        provider.save()
        provider.make_default()
        client = Client()
        client.force_login(user)
        with (
            tempfile.TemporaryDirectory() as directory,
            override_settings(DATA_DIR=Path(directory)),
        ):
            try:
                with (
                    patch.object(
                        jobs, "run_pipeline", side_effect=jobs.execute_pipeline
                    ),
                    patch.object(jobs.genai, "Client") as sdk,
                    patch.object(
                        jobs,
                        "run_transcription_pipeline_raw",
                        return_value="SPEAKER_1: Synthetic provider result",
                    ),
                ):
                    response = client.post(
                        "/jobs",
                        {"file": SimpleUploadedFile("voice.wav", b"fake audio")},
                    )
                    self.assertEqual(response.status_code, 202)
                    job_id = response.json()["id"]
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        status = client.get(f"/jobs/{job_id}").json()["status"]
                        if status in {"completed", "failed"}:
                            break
                        time.sleep(0.02)
                    self.assertEqual(status, "completed")
                    self.assertEqual(
                        client.get(f"/jobs/{job_id}/result").text,
                        "SPEAKER_1: Synthetic provider result",
                    )
                    sdk.assert_called_once_with(api_key="fake-queue-provider-key")
            finally:
                runtime.stop_queue()
