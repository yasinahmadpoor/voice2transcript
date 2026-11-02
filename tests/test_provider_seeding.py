"""Seeding, database-only configuration, and old job/checkpoint compatibility."""

import io
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase

from apps.transcriptions.defaults import transcription_defaults
from apps.transcriptions.models import ModelProvider
from apps.transcriptions.services import jobs, pipeline, providers
from apps.transcriptions.services.checkpoints import ChunkCheckpoint
from apps.transcriptions.services.queue import JobQueue
from apps.transcriptions.services.seeding import GEMINI_SEED_KEY, seed_gemini_provider
from common.http import APIError
from scripts import transcribe as cli


class ProviderSeedingTests(TestCase):
    def seed(self, key="fake-bootstrap-key"):
        with patch.dict(os.environ, {"GEMINI_API_KEY": key, "GOOGLE_API_KEY": ""}):
            return seed_gemini_provider()

    def test_seed_imports_encrypted_key_and_all_provider_defaults_once(self):
        output = io.StringIO()
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "fake-bootstrap-key",
                "GOOGLE_API_KEY": "other-fake-key",
            },
        ):
            call_command("seed_providers", stdout=output)
        provider = ModelProvider.objects.get(seed_key=GEMINI_SEED_KEY)
        self.assertEqual(provider.get_api_key(), "fake-bootstrap-key")
        self.assertNotIn("fake-bootstrap-key", provider.api_key_encrypted)
        self.assertNotIn("fake-bootstrap-key", output.getvalue())
        self.assertEqual(
            (provider.kind, provider.base_url, provider.model, provider.fallback_model),
            ("gemini", "", pipeline.DEFAULT_MODEL, pipeline.DEFAULT_FALLBACK_MODEL),
        )
        self.assertTrue(provider.is_enabled)
        self.assertTrue(provider.is_default)
        self.assertEqual(
            providers.available_providers(),
            [
                {
                    "id": str(provider.pk),
                    "name": provider.name,
                    "kind": "gemini",
                    "model": provider.model,
                    "fallback_model": provider.fallback_model,
                }
            ],
        )
        self.assertEqual(transcription_defaults()["provider"], str(provider.pk))

    def test_repeat_seed_never_overwrites_admin_edits_or_reimports_a_cleared_key(self):
        provider, created = self.seed()
        self.assertTrue(created)
        provider.name = "Renamed in admin"
        provider.model = "edited-primary"
        provider.fallback_model = "edited-fallback"
        provider.base_url = "https://changed.example.test"
        provider.is_default = False
        provider.is_enabled = False
        provider.set_api_key("fake-rotated-key")
        provider.save()
        before = list(ModelProvider.objects.values())
        _, created = self.seed("fake-replacement-env-key")
        self.assertFalse(created)
        self.assertEqual(list(ModelProvider.objects.values()), before)
        provider.set_api_key("")
        provider.save()
        _, created = self.seed()
        provider.refresh_from_db()
        self.assertFalse(created)
        self.assertEqual(provider.get_api_key(), "")
        self.assertEqual(ModelProvider.objects.count(), 1)

    def test_existing_default_and_name_collision_are_preserved(self):
        existing = ModelProvider.objects.create(
            name="Gemini",
            kind="openai",
            base_url="https://example.test/v1",
            model="custom",
            is_default=True,
        )
        provider, created = self.seed()
        self.assertTrue(created)
        self.assertNotEqual(provider.name, existing.name)
        self.assertFalse(provider.is_default)
        self.assertEqual(providers.resolve_provider().pk, existing.pk)
        self.assertEqual(providers.resolve_provider("env").pk, provider.pk)

    def test_google_key_alias_and_missing_key_setup(self):
        with patch.dict(
            os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": "fake-google-key"}
        ):
            provider, _ = seed_gemini_provider()
        self.assertEqual(provider.get_api_key(), "fake-google-key")
        provider.delete()  # Disposable test database only.
        provider, _ = self.seed("")
        self.assertFalse(provider.is_enabled)
        self.assertFalse(provider.is_default)
        self.assertEqual(providers.available_providers(), [])
        with self.assertRaises(APIError) as error:
            transcription_defaults()
        self.assertEqual(error.exception.status_code, 503)
        with self.assertRaises(APIError):
            providers.resolve_provider("env")
        user = get_user_model().objects.create_superuser(
            "seed-admin", password="test-only-password"
        )
        client = Client()
        client.force_login(user)
        response = client.post(
            f"/admin/web/modelprovider/{provider.pk}/change/",
            {
                "name": provider.name,
                "kind": "gemini",
                "model": "saved-primary",
                "api_key": "fake-entered-key",
                "is_enabled": "on",
                "_save": "Save",
            },
        )
        self.assertEqual(response.status_code, 302)
        provider.refresh_from_db()
        self.assertTrue(provider.is_default)
        self.assertEqual(provider.get_api_key(), "fake-entered-key")

    def test_bad_bootstrap_key_is_rejected_without_printing_it(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "fake-sensitive\nvalue"}):
            with self.assertRaises(CommandError) as error:
                call_command("seed_providers", stdout=io.StringIO())
        self.assertNotIn("fake-sensitive", str(error.exception))
        self.assertEqual(ModelProvider.objects.count(), 0)

    def test_web_uses_saved_key_and_model_even_with_different_environment_values(self):
        provider, _ = self.seed()
        provider.model = "database-primary"
        provider.fallback_model = "database-fallback"
        provider.save()
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "fake-different-key"}),
            patch.object(jobs.genai, "Client") as sdk,
            patch.object(
                jobs, "run_transcription_pipeline_raw", return_value="Transcript"
            ) as adapter,
        ):
            defaults = transcription_defaults()
            self.assertEqual(defaults["model"], "database-primary")
            jobs.execute_pipeline(
                Path("unused"),
                defaults["model"],
                0,
                "",
                defaults["fallback_model"],
                "Prompt",
                str(provider.pk),
            )
        self.assertEqual(sdk.call_args.kwargs["api_key"], "fake-bootstrap-key")
        self.assertEqual(adapter.call_args.kwargs["model"], "database-primary")
        provider.set_api_key("")
        provider.save()
        with (
            patch.dict(
                os.environ, {"GEMINI_API_KEY": "fake-fallback-must-not-be-used"}
            ),
            patch.object(jobs.genai, "Client") as sdk,
        ):
            with self.assertRaisesRegex(providers.ProviderError, "API key"):
                jobs.execute_pipeline(
                    Path("unused"), "primary", 0, "", "", "Prompt", str(provider.pk)
                )
        sdk.assert_not_called()

    def test_legacy_job_keeps_saved_checkpoint_when_env_alias_resolves_to_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            queue = JobQueue(folder, lambda *args: "unused")
            source = queue.uploads / "legacy.media"
            source.write_bytes(b"Original recording")
            chunks = []
            for index in range(2):
                path = folder / f"{index}.wav"
                path.write_bytes(f"Chunk {index}".encode())
                chunks.append((path, float(index)))
            parameters = {
                "model": "legacy-model",
                "chunk_minutes": 1,
                "language_hint": "fa",
                "fallback_model": "legacy-fallback",
                "prompt": "Original instructions",
            }
            queue.enqueue("legacy", "owner", "legacy.wav", source, parameters)
            started = time.time()
            with queue._db() as database:
                database.execute(
                    "UPDATE jobs SET status='processing',started_at=?", (started,)
                )
                before = database.execute("SELECT settings FROM jobs").fetchone()[0]
            context = {
                "database": str(queue.database),
                "job_id": "legacy",
                "started_at": started,
            }
            checkpoint = ChunkCheckpoint(
                context,
                source,
                parameters | {"provider": "env", "kind": "gemini", "base_url": ""},
            )
            checkpoint.prepare(chunks)
            checkpoint.save(0, "Already transcribed")
            provider, _ = self.seed()
            ModelProvider.objects.create(
                name="Different default",
                kind="openai",
                model="custom",
                is_default=False,
            ).make_default()
            with (
                patch.object(jobs.genai, "Client") as sdk,
                patch.object(pipeline, "convert_to_wav"),
                patch.object(pipeline, "split_wav", return_value=chunks),
                patch.object(
                    pipeline, "transcribe_chunk_raw", return_value="Remaining chunk"
                ) as transcribe,
            ):
                result = jobs.execute_pipeline(
                    source, **parameters, checkpoint_context=context
                )
            self.assertEqual(result, "Already transcribed\n\nRemaining chunk")
            self.assertEqual(transcribe.call_count, 1)
            self.assertEqual(transcribe.call_args.kwargs["chunk_path"], chunks[1][0])
            self.assertEqual(transcribe.call_args.kwargs["model"], "legacy-model")
            self.assertEqual(sdk.call_args.kwargs["api_key"], provider.get_api_key())
            with queue._db() as database:
                self.assertEqual(
                    database.execute("SELECT settings FROM jobs").fetchone()[0], before
                )

    def test_cli_uses_saved_seed_defaults_and_explicit_model_overrides(self):
        provider, _ = self.seed()
        provider.model = "database-primary"
        provider.fallback_model = "database-fallback"
        provider.save()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "voice.wav"
            source.write_bytes(b"Test audio")
            for options, expected in (
                ([], ("database-primary", "database-fallback")),
                (["--model", "override", "--fallback-model", ""], ("override", "")),
            ):
                with (
                    patch("sys.argv", ["transcribe", str(source), *options]),
                    patch.dict(os.environ, {"GEMINI_API_KEY": "fake-env-key"}),
                    patch.object(cli, "require_binary"),
                    patch.object(cli.genai, "Client", return_value=MagicMock()) as sdk,
                    patch.object(
                        cli,
                        "run_transcription_pipeline",
                        return_value={"language": "en", "segments": []},
                    ) as adapter,
                    patch.object(cli, "write_outputs"),
                    patch("builtins.print"),
                ):
                    cli.main()
                self.assertEqual(sdk.call_args.kwargs["api_key"], "fake-bootstrap-key")
                self.assertEqual(
                    (
                        adapter.call_args.kwargs["model"],
                        adapter.call_args.kwargs["fallback_model"],
                    ),
                    expected,
                )

    def test_seeded_provider_is_visible_and_can_be_selected_as_default_in_admin(self):
        provider, _ = self.seed()
        custom = ModelProvider.objects.create(
            name="Custom",
            kind="openai",
            model="custom",
            base_url="https://example.test/v1",
        )
        custom.make_default()
        admin = get_user_model().objects.create_superuser(
            "provider-admin", password="test-only-password"
        )
        client = Client()
        client.force_login(admin)
        listing = client.get("/admin/web/modelprovider/").content.decode()
        self.assertIn("Gemini", listing)
        self.assertIn("Use seeded Gemini as the default", listing)
        page = client.get(
            f"/admin/web/modelprovider/{provider.pk}/change/"
        ).content.decode()
        self.assertIn('id="provider-test-button"', page)
        self.assertIn(GEMINI_SEED_KEY, page)
        self.assertNotIn("fake-bootstrap-key", page)
        self.assertNotIn(provider.api_key_encrypted, page)
        self.assertEqual(
            client.post(
                "/admin/web/modelprovider/",
                {
                    "action": "use_seeded_gemini_default",
                    "_selected_action": str(custom.pk),
                },
            ).status_code,
            302,
        )
        self.assertEqual(providers.resolve_provider().pk, provider.pk)
