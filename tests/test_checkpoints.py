"""Checkpoint recovery, invalidation, ownership-independent storage, and adapters."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase

from apps.transcriptions.models import ModelProvider
from apps.transcriptions.services import jobs, pipeline, providers
from apps.transcriptions.services.checkpoints import (
    CheckpointMismatch,
    ChunkCheckpoint,
    current_checkpoint,
    transcribe_chunks,
)
from apps.transcriptions.services.execution import JobCancelled
from apps.transcriptions.services.queue import FAILED_UPLOAD_TTL, JobQueue


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.queue = JobQueue(self.folder, lambda *args: "unused")
        self.path = self.queue.uploads / f"{uuid4().hex}.media"
        self.path.write_bytes(b"Original recording")
        self.chunks = []
        for index in range(3):
            path = self.folder / f"chunk{index}.wav"
            path.write_bytes(f"audio{index}".encode())
            self.chunks.append((path, float(index)))
        self.settings = {
            "model": "primary",
            "chunk_minutes": 1,
            "language_hint": "fa",
            "fallback_model": "",
            "prompt": "Verbatim",
        }
        self.job = self.queue.enqueue(
            self.path.stem,
            "owner",
            "Recording.wav",
            self.path,
            self.settings,
            "stable-key",
        )

    def context(self, row):
        return {
            "database": str(self.queue.database),
            "job_id": row["id"],
            "started_at": row["started_at"],
        }

    def checkpoint(self, context, **configuration):
        return ChunkCheckpoint(
            context,
            self.path,
            {"model": "primary", "prompt": "Verbatim", **configuration},
        )

    def process(self):
        with patch("apps.transcriptions.services.queue.logger.exception"):
            self.queue._process(self.queue._claim())

    def test_resume_failed_chunks_keeps_order_and_cleans_success(self):
        calls = []

        def transcribe(path):
            calls.append(path.stem)
            if calls == ["chunk0", "chunk1"]:
                raise RuntimeError("Synthetic failure")
            return f"Text {path.stem}"

        def runner(*args):
            return transcribe_chunks(
                self.chunks, transcribe, self.checkpoint(current_checkpoint())
            )

        self.queue.runner = runner
        self.process()
        failed = self.queue.get(self.job["id"], "owner")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["progress"], {"completed_chunks": 1, "total_chunks": 3})
        self.assertTrue(failed["resumable"])
        self.queue = JobQueue(self.folder, runner)
        self.queue.retry(self.job["id"], "owner")
        self.process()
        self.assertEqual(calls, ["chunk0", "chunk1", "chunk1", "chunk2"])
        self.assertEqual(
            self.queue.result(self.job["id"], "owner"),
            "Text chunk0\n\nText chunk1\n\nText chunk2",
        )
        self.assertFalse(self.path.exists())
        with self.queue._db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM job_checkpoints").fetchone()[0], 0
            )

    def test_all_saved_chunks_reassemble_after_parent_crash_without_provider_calls(
        self,
    ):
        row = self.queue._claim()
        checkpoint = self.checkpoint(self.context(row))
        self.assertEqual(
            transcribe_chunks(self.chunks, lambda path: path.stem, checkpoint),
            "chunk0\n\nchunk1\n\nchunk2",
        )
        self.queue = JobQueue(
            self.folder,
            lambda *args: transcribe_chunks(
                self.chunks,
                lambda path: self.fail("Saved chunk repeated"),
                self.checkpoint(current_checkpoint()),
            ),
        )
        self.queue.start()
        try:
            failed = self.queue.get(row["id"], "owner")
            self.assertTrue(failed["interrupted"])
            self.assertEqual(failed["progress"]["completed_chunks"], 3)
            self.queue.retry(row["id"], "owner")
            deadline = time.monotonic() + 5
            while (
                self.queue.get(row["id"], "owner")["status"] != "completed"
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertEqual(
                self.queue.result(row["id"], "owner"), "chunk0\n\nchunk1\n\nchunk2"
            )
        finally:
            self.queue.stop()

    def test_configuration_audio_and_boundaries_must_match(self):
        row = self.queue._claim()
        context = self.context(row)
        checkpoint = self.checkpoint(context)
        checkpoint.prepare(self.chunks)
        checkpoint.save(0, "Saved text")
        for change in (
            {"model": "different"},
            {"prompt": "different"},
            {"base_url": "https://different.example"},
            {"chunk_minutes": 2},
        ):
            with self.subTest(change=change), self.assertRaises(CheckpointMismatch):
                self.checkpoint(context, **change).prepare(self.chunks)
        self.path.write_bytes(b"Changed recording")
        with self.assertRaises(CheckpointMismatch):
            self.checkpoint(context).prepare(self.chunks)
        self.path.write_bytes(b"Original recording")
        with self.assertRaises(CheckpointMismatch):
            self.checkpoint(context).prepare(
                [(path, offset + 1) for path, offset in self.chunks]
            )
        self.chunks[0][0].write_bytes(b"Changed audio chunk")
        with self.assertRaises(CheckpointMismatch):
            self.checkpoint(context).prepare(self.chunks)

    def test_cancelled_and_obsolete_attempts_cannot_save_and_restart_clears_progress(
        self,
    ):
        row = self.queue._claim()
        checkpoint = self.checkpoint(self.context(row))
        checkpoint.prepare(self.chunks)
        checkpoint.save(0, "Saved text")
        self.queue.cancel(row["id"], "owner")
        with self.assertRaises(JobCancelled):
            checkpoint.save(1, "Late response")
        with self.queue._db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (row["id"],))
        self.queue.retry(row["id"], "owner")
        newer = self.queue._claim()
        with self.assertRaises(JobCancelled):
            checkpoint.save(1, "Obsolete attempt")
        restored = self.checkpoint(self.context(newer))
        restored.prepare(self.chunks)
        self.assertEqual(restored.completed, {"0": "Saved text"})
        self.queue.cancel(row["id"], "owner")
        with self.queue._db() as db:
            db.execute("UPDATE jobs SET status='cancelled' WHERE id=?", (row["id"],))
        restarted = self.queue.retry(row["id"], "owner", restart=True)
        self.assertEqual(restarted["progress"]["completed_chunks"], 0)
        self.assertEqual(
            self.queue.find_request("owner", "stable-key")["id"], row["id"]
        )
        with self.queue._db() as db:
            self.assertEqual(
                json.loads(
                    db.execute(
                        "SELECT settings FROM jobs WHERE id=?", (row["id"],)
                    ).fetchone()[0]
                ),
                self.settings,
            )

    def test_cleanup_and_delete_remove_saved_text(self):
        row = self.queue._claim()
        checkpoint = self.checkpoint(self.context(row))
        checkpoint.prepare(self.chunks)
        checkpoint.save(0, "Saved text")
        with self.queue._db() as db:
            db.execute(
                "UPDATE jobs SET status='failed',finished_at=? WHERE id=?",
                (time.time() - FAILED_UPLOAD_TTL - 1, row["id"]),
            )
        self.queue.cleanup_failed_uploads()
        self.assertFalse(self.queue.get(row["id"], "owner")["retryable"])
        with self.queue._db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM job_checkpoints").fetchone()[0], 0
            )
        self.path.write_bytes(b"Original recording")
        self.queue.retry(row["id"], "owner")
        row = self.queue._claim()
        checkpoint = self.checkpoint(self.context(row))
        checkpoint.prepare(self.chunks)
        checkpoint.save(0, "Saved again")
        with self.queue._db() as db:
            db.execute("UPDATE jobs SET status='failed' WHERE id=?", (row["id"],))
        self.queue.delete(row["id"], "owner")
        with self.queue._db() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM job_checkpoints").fetchone()[0], 0
            )


class ProviderCheckpointTests(TestCase):
    def test_all_adapters_resume_saved_chunks_and_allow_api_key_rotation(self):
        for kind, base_url in [
            ("openai", "https://audio.example/v1"),
            ("gemini", "https://proxy.example"),
            ("gemini", ""),
        ]:
            with (
                self.subTest(kind=kind, url=base_url),
                tempfile.TemporaryDirectory() as directory,
            ):
                provider = ModelProvider.objects.create(
                    name=f"Provider {kind} {base_url}",
                    kind=kind,
                    base_url=base_url,
                    model="primary",
                )
                provider.set_api_key("fake-first-key")
                provider.save()
                queue = JobQueue(Path(directory), lambda *args: "unused")
                source = queue.uploads / f"{uuid4().hex}.media"
                source.write_bytes(b"Original recording")
                chunks = []
                for index in range(3):
                    path = Path(directory) / f"{index}.wav"
                    path.write_bytes(str(index).encode())
                    chunks.append((path, float(index)))
                parameters = {
                    "model": "primary",
                    "chunk_minutes": 1,
                    "language_hint": "fa",
                    "fallback_model": "",
                    "prompt": "Verbatim",
                    "provider": str(provider.pk),
                }
                queue.enqueue(source.stem, "owner", "Test.wav", source, parameters)
                row = queue._claim()
                context = {
                    "database": str(queue.database),
                    "job_id": source.stem,
                    "started_at": row["started_at"],
                }
                calls = []

                def transcribe(index):
                    calls.append(index)
                    if calls == [0, 1]:
                        raise RuntimeError("Synthetic failure")
                    return f"Text {index}"

                with (
                    patch.object(jobs.genai, "Client"),
                    patch.object(pipeline, "convert_to_wav"),
                    patch.object(pipeline, "split_wav", return_value=chunks),
                    patch.object(providers, "convert_to_wav"),
                    patch.object(providers, "split_wav", return_value=chunks),
                    patch.object(
                        pipeline,
                        "transcribe_chunk_raw",
                        side_effect=lambda **kwargs: transcribe(
                            int(kwargs["chunk_path"].stem)
                        ),
                    ),
                    patch.object(
                        providers,
                        "_request_chunk",
                        side_effect=lambda client, selected, path, *args: transcribe(
                            int(path.stem)
                        ),
                    ),
                    patch.object(
                        providers,
                        "generate_transcription_response_raw",
                        side_effect=lambda client,
                        model,
                        prompt,
                        audio: SimpleNamespace(
                            text=transcribe(int(audio.inline_data.data))
                        ),
                    ),
                ):
                    with self.assertRaises(providers.ProviderError):
                        jobs.execute_pipeline(
                            source, **parameters, checkpoint_context=context
                        )
                    self.assertEqual(
                        queue.get(source.stem, "owner")["progress"]["completed_chunks"],
                        1,
                    )
                    provider.set_api_key("fake-rotated-key")
                    provider.save()
                    self.assertEqual(
                        jobs.execute_pipeline(
                            source, **parameters, checkpoint_context=context
                        ),
                        "Text 0\n\nText 1\n\nText 2",
                    )
                    self.assertEqual(calls, [0, 1, 1, 2])
                with queue._db() as db:
                    saved = str(db.execute("SELECT * FROM job_checkpoints").fetchall())
                    self.assertNotIn("fake-first-key", saved)
                    self.assertNotIn("fake-rotated-key", saved)
