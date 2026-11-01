import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from apps.transcriptions.services.queue import (
    FAILED_UPLOAD_TTL,
    JobConflict,
    JobQueue,
    JobNotFound,
    QueueFull,
)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.queues = []
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        for queue in self.queues:
            queue.stop(timeout=5)
        self.temp.cleanup()

    def queue(self, runner=lambda *args: "Transcript", **kwargs):
        queue = JobQueue(self.directory, runner, **kwargs)
        self.queues.append(queue)
        return queue

    def enqueue(self, queue, filename="voice.wav"):
        job_id = uuid4().hex
        path = queue.uploads / f"{job_id}.media"
        path.write_bytes(b"test recording")
        settings = {
            "model": "primary",
            "chunk_minutes": 0,
            "language_hint": "",
            "fallback_model": "",
            "prompt": "Verbatim",
        }
        try:
            return queue.enqueue(job_id, "owner", filename, path, settings)
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("Timed out waiting for queue state")

    def test_two_workers_limit_and_fifo_queue(self):
        active = 0
        peak = 0
        started = []
        lock = threading.Lock()
        two_started = threading.Event()

        def runner(path, *settings):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                started.append(path.stem)
                if active == 2:
                    two_started.set()
            self.release.wait(5)
            with lock:
                active -= 1
            return "Transcript"

        queue = self.queue(runner)
        jobs = [self.enqueue(queue) for _ in range(5)]
        self.assertEqual(
            [queue.get(job["id"], "owner")["queue_position"] for job in jobs],
            [1, 2, 3, 4, 5],
        )
        queue.start()
        self.assertTrue(two_started.wait(5))
        self.assertEqual(set(started), {job["id"] for job in jobs[:2]})
        self.assertEqual(queue.list("owner")["active"], 2)
        self.assertEqual(queue.list("owner")["queued"], 3)
        self.assertEqual(queue.get(jobs[2]["id"], "owner")["queue_position"], 1)
        with self.assertRaises(JobConflict):
            queue.delete(jobs[0]["id"], "owner")
        self.release.set()
        self.wait(
            lambda: all(
                queue.get(job["id"], "owner")["status"] == "completed" for job in jobs
            )
        )
        self.wait(lambda: not list(queue.uploads.iterdir()))
        self.assertEqual(peak, 2)

    def test_restart_recovers_queued_jobs_and_marks_running_interrupted(self):
        old_queue = self.queue()
        queued = self.enqueue(old_queue)
        interrupted = self.enqueue(old_queue)
        with old_queue._db() as db:
            db.execute(
                "UPDATE jobs SET status='processing', started_at=? WHERE id=?",
                (time.time(), interrupted["id"]),
            )
        orphan = old_queue.uploads / "incomplete.part"
        orphan.write_bytes(b"partial")
        restored = self.queue()
        restored.start()
        self.wait(lambda: restored.get(queued["id"], "owner")["status"] == "completed")
        failed = restored.get(interrupted["id"], "owner")
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["interrupted"])
        self.assertTrue(failed["retryable"])
        self.assertFalse(orphan.exists())
        restored.retry(interrupted["id"], "owner")
        self.wait(
            lambda: restored.get(interrupted["id"], "owner")["status"] == "completed"
        )
        restored.stop()
        reopened = self.queue()
        reopened.start()
        self.assertEqual(reopened.result(queued["id"], "owner"), "Transcript")
        self.assertEqual(reopened.result(interrupted["id"], "owner"), "Transcript")

    def test_second_process_lock_does_not_interrupt_running_jobs(self):
        running = threading.Event()

        def runner(*args):
            running.set()
            self.release.wait(5)
            return "Transcript"

        first = self.queue(runner)
        first.start()
        job = self.enqueue(first)
        self.assertTrue(running.wait(5))
        second = self.queue()
        with self.assertRaisesRegex(RuntimeError, "one web server worker process"):
            second.start()
        self.assertEqual(first.get(job["id"], "owner")["status"], "processing")

    def test_capacity_cancel_and_failed_upload_expiration(self):
        queue = self.queue(max_pending=2)
        first = self.enqueue(queue)
        second = self.enqueue(queue)
        with self.assertRaises(QueueFull):
            self.enqueue(queue)
        queue.delete(first["id"], "owner")
        third = self.enqueue(queue)
        self.assertEqual(queue.get(second["id"], "owner")["queue_position"], 1)
        with queue._db() as db:
            db.execute(
                "UPDATE jobs SET status='failed', finished_at=? WHERE id=?",
                (time.time() - FAILED_UPLOAD_TTL - 1, third["id"]),
            )
        queue.cleanup_failed_uploads()
        self.assertFalse(queue.get(third["id"], "owner")["retryable"])
        with self.assertRaises(JobConflict):
            queue.retry(third["id"], "owner")

    def test_retry_preserves_original_parameters(self):
        calls = []

        def runner(path, *settings):
            calls.append(settings)
            if len(calls) == 1:
                raise RuntimeError("Provider error")
            return "Retried transcript"

        queue = self.queue(runner)
        with patch("apps.transcriptions.services.queue.logger.exception"):
            queue.start()
            job = self.enqueue(queue)
            self.wait(lambda: queue.get(job["id"], "owner")["status"] == "failed")
        queue.retry(job["id"], "owner")
        self.wait(lambda: queue.get(job["id"], "owner")["status"] == "completed")
        self.assertEqual(calls, [("primary", 0, "", "", "Verbatim")] * 2)
        self.assertEqual(queue.result(job["id"], "owner"), "Retried transcript")

    def test_existing_database_is_migrated_and_retry_keys_survive_reopening(self):
        database = self.directory / "jobs.sqlite3"
        with sqlite3.connect(database) as db:
            db.execute("""CREATE TABLE jobs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, filename TEXT NOT NULL,
                input_path TEXT NOT NULL, settings TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, queued_at REAL NOT NULL, started_at REAL,
                finished_at REAL, transcript TEXT, error TEXT, interrupted INTEGER NOT NULL DEFAULT 0
            )""")
        queue = self.queue()
        job = self.enqueue(queue)
        with queue._db() as db:
            db.execute(
                "UPDATE jobs SET request_key='persistent-key' WHERE id=?", (job["id"],)
            )
        reopened = self.queue()
        self.assertEqual(
            reopened.find_request("owner", "persistent-key")["id"], job["id"]
        )
        self.assertIsNone(reopened.find_request("another-owner", "persistent-key"))
        self.assertIsNone(reopened.find_request("owner", "missing-key"))

    def test_queued_cancellation_preserves_history_and_retry_identity(self):
        queue = self.queue(max_pending=2)
        job = self.enqueue(queue)
        self.enqueue(queue)
        with self.assertRaises(JobNotFound):
            queue.cancel(job["id"], "someone-else")
        cancelled = queue.cancel(job["id"], "owner")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["retryable"])
        self.assertEqual(queue.cancel(job["id"], "owner"), cancelled)
        self.assertEqual(queue.list("owner")["queued"], 1)
        self.assertEqual(queue.retry(job["id"], "owner")["status"], "queued")
        queue.cancel(job["id"], "owner")
        with queue._db() as db:
            db.execute(
                "UPDATE jobs SET finished_at=? WHERE id=?",
                (time.time() - FAILED_UPLOAD_TTL - 1, job["id"]),
            )
        queue.cleanup_failed_uploads()
        self.assertFalse(queue.get(job["id"], "owner")["retryable"])

    def test_cancel_wins_completion_race_and_survives_restart(self):
        queue = self.queue()
        job = self.enqueue(queue)

        def runner(*args):
            queue.cancel(job["id"], "owner")
            return "Must not be published"

        queue.runner = runner
        queue.start()
        self.wait(lambda: queue.get(job["id"], "owner")["status"] == "cancelled")
        with self.assertRaises(JobConflict):
            queue.result(job["id"], "owner")
        with queue._db() as db:
            self.assertIsNone(
                db.execute(
                    "SELECT transcript FROM jobs WHERE id=?", (job["id"],)
                ).fetchone()[0]
            )
        queue.stop()
        with queue._db() as db:
            db.execute("UPDATE jobs SET status='cancelling' WHERE id=?", (job["id"],))
        restored = self.queue()
        restored.start()
        self.assertEqual(restored.get(job["id"], "owner")["status"], "cancelled")

    def test_completed_job_cannot_be_cancelled(self):
        queue = self.queue()
        job = self.enqueue(queue)
        queue.start()
        self.wait(lambda: queue.get(job["id"], "owner")["status"] == "completed")
        with self.assertRaises(JobConflict):
            queue.cancel(job["id"], "owner")
        self.assertEqual(queue.result(job["id"], "owner"), "Transcript")
