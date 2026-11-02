"""Meeting hints, prompt snapshots, private history, and legacy retry behavior."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase

from apps.transcriptions.defaults import API_DEFAULT_PROMPT
from apps.transcriptions.forms import PromptContextForm
from apps.transcriptions.models import ModelProvider
from apps.transcriptions.services import jobs
from apps.transcriptions.services.prompts import compose_prompt
from apps.transcriptions.services.checkpoints import (
    ChunkCheckpoint,
    current_checkpoint,
    transcribe_chunks,
)
from apps.transcriptions.services.queue import JobQueue


class MeetingValidationTests(SimpleTestCase):
    def form(self, **fields):
        return PromptContextForm(
            {"prompt": "Instructions", "meeting_description": "", "participants": []}
            | fields
        )

    def test_empty_context_keeps_exact_original_prompt(self):
        self.assertTrue(self.form().is_valid())
        prompt = "  Original\nJSON instructions\n"
        self.assertEqual(compose_prompt(prompt), prompt)
        self.assertEqual(compose_prompt(prompt, "", []), prompt)

    def test_context_normalizes_blank_rows_and_preserves_unicode_and_literal_markup(
        self,
    ):
        form = self.form(
            meeting_description="  جلسه پروژه  ",
            participants=json.dumps(
                [
                    {},
                    {"name": " زهرا ", "description": " مدیر پروژه "},
                    {"name": "<script>alert(1)</script>", "description": ""},
                ]
            ),
        )
        self.assertTrue(form.is_valid(), form.errors)
        fields = form.cleaned_data
        self.assertEqual(fields["meeting_description"], "جلسه پروژه")
        self.assertEqual(
            fields["participants"][0], {"name": "زهرا", "description": "مدیر پروژه"}
        )
        final = compose_prompt(**fields)
        self.assertTrue(final.startswith("Instructions\n\n"))
        self.assertIn("جلسه پروژه", final)
        self.assertIn("<script>alert(1)</script>", final)
        self.assertIn("otherwise use generic SPEAKER_1", final)
        self.assertIn("Follow the output format", final)

    def test_limits_and_malformed_participants_are_rejected(self):
        cases = [
            {"meeting_description": "m" * 5001},
            {"participants": "{"},
            {"participants": "null"},
            {"participants": {}},
            {"participants": ["Name"]},
            {"participants": [{"name": 12}]},
            {"participants": [{"name": "Name", "description": []}]},
            {"participants": [{"name": "Name", "unexpected": True}]},
            {"participants": [{"description": "A description needs a name"}]},
            {"participants": [{"name": "n" * 101}]},
            {"participants": [{"name": "Name", "description": "d" * 1001}]},
            {"participants": [{"name": "Name"}] * 21},
        ]
        for fields in cases:
            with self.subTest(fields=list(fields)):
                self.assertFalse(self.form(**fields).is_valid())
        self.assertTrue(
            self.form(
                meeting_description="m" * 5000,
                participants=[{"name": "n" * 100, "description": "d" * 1000}] * 20,
            ).is_valid()
        )


class MeetingContextAPITests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.calls = []
        self.fail = False

        def runner(*args, **kwargs):
            self.calls.append((args, kwargs))
            if self.fail:
                raise RuntimeError("Synthetic failure")
            return "A transcript"

        self.queue = JobQueue(Path(self.directory.name), runner)
        self.queue_patch = patch(
            "apps.transcriptions.views.get_queue", return_value=self.queue
        )
        self.queue_patch.start()
        self.addCleanup(self.queue_patch.stop)
        self.user = get_user_model().objects.create_user("meeting-owner")
        self.client = Client()
        self.client.force_login(self.user)
        self.owner = f"user:{self.user.pk}"
        self.provider = ModelProvider.objects.create(
            name="Provider", kind="gemini", model="primary", is_default=True
        )
        self.context = {
            "prompt": "Return JSON only.",
            "meeting_description": "Quarterly planning",
            "participants": [{"name": "Sara", "description": "Project lead"}],
        }

    def submit(self, **extra):
        data = self.context | extra
        data["participants"] = json.dumps(data["participants"])
        return self.client.post(
            "/jobs", {"file": SimpleUploadedFile("meeting.wav", b"audio"), **data}
        )

    def test_preview_matches_saved_and_executed_prompt_without_creating_a_job(self):
        preview = self.client.post(
            "/transcription/prompt-preview",
            json.dumps(self.context),
            content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self.queue.list(self.owner)["jobs"], [])
        self.assertEqual(list(self.queue.uploads.iterdir()), [])
        self.assertEqual(self.calls, [])
        response = self.submit(
            effective_prompt="Client cannot override the compiled prompt"
        )
        self.assertEqual(response.status_code, 202, response.content)
        job = response.json()
        saved = self.client.get(f"/jobs/{job['id']}/context").json()
        self.assertEqual(saved, self.context | preview.json())
        self.assertNotIn("meeting_description", job)
        self.assertNotIn("participants", self.client.get("/jobs").json()["jobs"][0])
        self.queue._process(self.queue._claim())
        self.assertEqual(self.calls[0][0][-1], preview.json()["effective_prompt"])
        self.assertNotIn("Client cannot", self.calls[0][0][-1])
        with self.queue._db() as db:
            settings = json.loads(db.execute("SELECT settings FROM jobs").fetchone()[0])
        self.assertEqual(settings["prompt"], self.context["prompt"])
        self.assertEqual(settings["participants"], self.context["participants"])

    def test_preview_auth_validation_and_cross_site_protection(self):
        anonymous = Client()
        self.assertEqual(
            anonymous.post(
                "/transcription/prompt-preview",
                b"invalid",
                content_type="application/json",
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/transcription/prompt-preview",
                "{}",
                content_type="application/json",
                HTTP_ORIGIN="https://other.example",
            ).status_code,
            403,
        )
        for body in (
            "{",
            "[]",
            '{"participants":null}',
            '{"meeting_description":42}',
            '{"prompt":{}}',
            '{"unexpected":true}',
            '{"participants":[{"description":"Missing name"}]}',
        ):
            with self.subTest(body=body):
                self.assertEqual(
                    self.client.post(
                        "/transcription/prompt-preview",
                        body,
                        content_type="application/json",
                    ).status_code,
                    422,
                )
        self.assertEqual(
            self.client.post(
                "/transcription/prompt-preview", "{}", content_type="application/json"
            ).json()["effective_prompt"],
            API_DEFAULT_PROMPT,
        )

    def test_saved_context_is_private_and_retry_uses_original_snapshot(self):
        job = self.submit().json()
        url = f"/jobs/{job['id']}/context"
        self.assertEqual(Client().get(url).status_code, 401)
        other = Client()
        other.force_login(get_user_model().objects.create_user("other-meeting-user"))
        self.assertEqual(other.get(url).status_code, 404)
        self.fail = True
        with patch("apps.transcriptions.services.queue.logger.exception"):
            self.queue._process(self.queue._claim())
        before = self.client.get(url).json()
        self.client.post(
            "/transcription/prompt-preview",
            json.dumps(
                self.context
                | {
                    "meeting_description": "Different meeting",
                    "prompt": "Different instructions",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(
            self.client.post(
                f"/jobs/{job['id']}/retry", "{}", content_type="application/json"
            ).status_code,
            202,
        )
        self.fail = False
        self.queue._process(self.queue._claim())
        self.assertEqual(self.calls[0][0][-1], self.calls[1][0][-1])
        self.assertEqual(self.client.get(url).json(), before)
        # A server/queue restart retains both raw context and the compiled prompt.
        reopened = JobQueue(Path(self.directory.name), lambda *args: "unused")
        self.assertEqual(reopened.context(job["id"], self.owner), before)

    def test_legacy_jobs_and_empty_new_context_keep_original_prompt(self):
        source = self.queue.uploads / "legacy.media"
        source.write_bytes(b"audio")
        settings = {
            "model": "old",
            "chunk_minutes": 0,
            "language_hint": "",
            "fallback_model": "",
            "prompt": "Original legacy prompt",
        }
        self.queue.enqueue("legacy", self.owner, "legacy.wav", source, settings)
        self.queue._process(self.queue._claim())
        self.assertEqual(self.calls[0][0][-1], settings["prompt"])
        self.assertEqual(
            self.client.get("/jobs/legacy/context").json(),
            {
                "meeting_description": "",
                "participants": [],
                "prompt": settings["prompt"],
                "effective_prompt": settings["prompt"],
            },
        )
        job = self.submit(
            meeting_description="", participants=[], prompt="Exact original"
        ).json()
        self.assertEqual(
            self.queue.context(job["id"], self.owner)["effective_prompt"],
            "Exact original",
        )

    def test_resume_reuses_saved_chunks_with_the_original_combined_prompt(self):
        chunks = []
        for index in range(2):
            part = Path(self.directory.name) / f"{index}.wav"
            part.write_bytes(f"Chunk {index}".encode())
            chunks.append((part, float(index)))
        requests = []

        def runner(source, *args, **kwargs):
            checkpoint = ChunkCheckpoint(
                current_checkpoint(), source, {"prompt": args[-1]}
            )

            def transcribe(part):
                index = int(part.stem)
                requests.append(index)
                if index == 1 and self.fail:
                    raise RuntimeError("Synthetic interrupted chunk")
                return f"Part {index}"

            return transcribe_chunks(chunks, transcribe, checkpoint)

        self.queue.runner = runner
        job = self.submit().json()
        original = self.queue.context(job["id"], self.owner)
        self.fail = True
        with patch("apps.transcriptions.services.queue.logger.exception"):
            self.queue._process(self.queue._claim())
        failed = self.queue.get(job["id"], self.owner)
        self.assertTrue(failed["resumable"])
        self.assertEqual(failed["progress"], {"completed_chunks": 1, "total_chunks": 2})
        self.context["meeting_description"] = "Edited form for the next meeting"
        self.context["prompt"] = "New instructions"
        self.queue.retry(job["id"], self.owner)
        self.fail = False
        self.queue._process(self.queue._claim())
        self.assertEqual(requests, [0, 1, 1])
        self.assertEqual(self.queue.result(job["id"], self.owner), "Part 0\n\nPart 1")
        self.assertEqual(self.queue.context(job["id"], self.owner), original)

    def test_invalid_upload_context_is_rejected_before_storing_recording(self):
        for extra in (
            {"participants": [{"description": "No name"}]},
            {"participants": [{"name": "Name"}] * 21},
            {"meeting_description": "m" * 5001},
        ):
            with self.subTest(extra=list(extra)):
                self.assertEqual(self.submit(**extra).status_code, 422)
        self.assertEqual(list(self.queue.uploads.iterdir()), [])

    def test_all_provider_routes_receive_same_compiled_context(self):
        final = compose_prompt(**self.context)
        self.provider.set_api_key("fake-only-test-key")
        for kind, base_url, target in (
            ("gemini", "", "run_transcription_pipeline_raw"),
            ("gemini", "https://example.test", "transcribe_gemini_inline"),
            ("openai", "https://example.test/v1", "transcribe_openai"),
        ):
            self.provider.kind = kind
            self.provider.base_url = base_url
            self.provider.save()
            with (
                patch.object(jobs.genai, "Client"),
                patch.object(jobs, target, return_value="Transcript") as adapter,
            ):
                jobs.execute_pipeline(
                    Path("unused"), "primary", 0, "", "", final, str(self.provider.pk)
                )
            if target == "run_transcription_pipeline_raw":
                self.assertEqual(adapter.call_args.kwargs["prompt"], final)
            else:
                self.assertEqual(adapter.call_args.args[6], final)
