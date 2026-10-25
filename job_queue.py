"""Persistent SQLite queue, owned by one API process with dedicated workers."""

import fcntl
import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)
FAILED_UPLOAD_TTL = 24 * 60 * 60
INTERRUPTED = "The server restarted while this recording was processing. Retry the job to start again."
FAILED = "Transcription failed. Check the recording and Gemini configuration, then retry."


class QueueFull(Exception):
    pass


class JobNotFound(Exception):
    pass


class JobConflict(Exception):
    pass


class JobQueue:
    def __init__(self, directory: Path, runner: Callable, workers: int = 2, max_pending: int = 20):
        if not 1 <= workers <= 16 or max_pending < workers:
            raise ValueError("TRANSCRIPTION_WORKERS must be 1–16 and MAX_PENDING_JOBS must be at least the worker count.")
        self.directory = Path(directory)
        self.uploads = self.directory / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.directory / "jobs.sqlite3"
        self.runner = runner
        self.workers = workers
        self.max_pending = max_pending
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._lock_file = None
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, owner TEXT NOT NULL, filename TEXT NOT NULL,
                input_path TEXT NOT NULL, settings TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, queued_at REAL NOT NULL,
                started_at REAL, finished_at REAL, transcript TEXT, error TEXT,
                interrupted INTEGER NOT NULL DEFAULT 0, request_key TEXT
            )""")
            if "request_key" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN request_key TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_request_key ON jobs(owner, request_key) WHERE request_key IS NOT NULL")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(status, queued_at, sequence)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_owner ON jobs(owner, sequence)")
        self.database.chmod(0o600)

    @contextmanager
    def _db(self):
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def start(self) -> None:
        if self._lock_file is not None:
            raise RuntimeError("Queue is already started.")
        lock_file = (self.directory / "worker.lock").open("a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            raise RuntimeError("Another queue process is using JOB_DATA_DIR. Run exactly one Uvicorn worker.") from exc
        self._lock_file = lock_file
        try:
            with self._db() as db:
                db.execute(
                    "UPDATE jobs SET status='failed', interrupted=1, error=?, finished_at=? WHERE status='processing'",
                    (INTERRUPTED, time.time()),
                )
                referenced = {row[0] for row in db.execute("SELECT input_path FROM jobs")}
            # Startup happens before accepting uploads, so uncommitted files are safe to remove.
            for path in self.uploads.iterdir():
                if path.is_file() and str(path) not in referenced:
                    path.unlink(missing_ok=True)
            self.cleanup_failed_uploads()
            self._stop.clear()
            self._threads = [threading.Thread(target=self._worker, name=f"transcription-{i + 1}", daemon=True) for i in range(self.workers)]
            for thread in self._threads:
                thread.start()
        except Exception:
            self.stop()
            raise

    def stop(self, timeout: float = 2) -> None:
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0, deadline - time.monotonic()))
        # A live worker retains the process lock until it ends or the process exits.
        if not any(thread.is_alive() for thread in self._threads) and self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def _check_capacity(self, db) -> None:
        count = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'processing')").fetchone()[0]
        if count >= self.max_pending:
            raise QueueFull("The transcription queue is full. Please try again later.")

    def check_capacity(self) -> None:
        with self._db() as db:
            self._check_capacity(db)

    def find_request(self, owner: str, request_key: str) -> dict | None:
        with self._db() as db:
            row = db.execute(self._select + " WHERE owner=? AND request_key=?", (owner, request_key)).fetchone()
        return self._public(row) if row is not None else None

    def enqueue(self, job_id: str, owner: str, filename: str, path: Path, settings: dict, request_key: str | None = None) -> dict:
        now = time.time()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_key is not None:
                existing = db.execute(self._select + " WHERE owner=? AND request_key=?", (owner, request_key)).fetchone()
                if existing is not None:
                    return self._public(existing)
            self._check_capacity(db)
            db.execute(
                "INSERT INTO jobs(id, owner, filename, input_path, settings, status, created_at, queued_at, request_key) VALUES(?,?,?,?,?,'queued',?,?,?)",
                (job_id, owner, filename, str(path), json.dumps(settings), now, now, request_key),
            )
        self._wake.set()
        return self.get(job_id, owner)

    def _claim(self):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY queued_at, sequence LIMIT 1").fetchone()
            if row is not None:
                db.execute("UPDATE jobs SET status='processing', started_at=? WHERE id=?", (time.time(), row["id"]))
            return row

    def _worker(self) -> None:
        next_cleanup = time.monotonic() + 60
        while not self._stop.is_set():
            try:
                if time.monotonic() >= next_cleanup:
                    self.cleanup_failed_uploads()
                    next_cleanup = time.monotonic() + 60
                row = self._claim()
                if self._stop.is_set():
                    if row is not None:
                        with self._db() as db:
                            db.execute("UPDATE jobs SET status='queued', started_at=NULL WHERE id=?", (row["id"],))
                    break
                if row is None:
                    self._wake.wait(0.5)
                    self._wake.clear()
                    continue
                self._process(row)
            except Exception:
                logger.exception("Queue worker failed")
                self._stop.wait(0.5)

    def _process(self, row) -> None:
        path = Path(row["input_path"])
        try:
            settings = json.loads(row["settings"])
            text = self.runner(path, settings["model"], settings["chunk_minutes"], settings["language_hint"], settings["fallback_model"], settings["prompt"])
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Transcription returned no text.")
        except Exception:
            logger.exception("Transcription job %s failed", row["id"])
            with self._db() as db:
                db.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?", (FAILED, time.time(), row["id"]))
            return
        with self._db() as db:
            db.execute("UPDATE jobs SET status='completed', transcript=?, error=NULL, finished_at=? WHERE id=?", (text, time.time(), row["id"]))
        path.unlink(missing_ok=True)

    @staticmethod
    def _public(row) -> dict:
        return {
            key: row[key] for key in ("id", "filename", "status", "created_at", "queued_at", "started_at", "finished_at", "error", "queue_position")
        } | {"interrupted": bool(row["interrupted"]), "retryable": row["status"] == "failed" and Path(row["input_path"]).is_file()}

    _select = """SELECT id, owner, filename, status, input_path, created_at, queued_at, started_at, finished_at, error, interrupted,
        CASE WHEN status='queued' THEN (SELECT COUNT(*) FROM jobs q WHERE q.status='queued'
          AND (q.queued_at < jobs.queued_at OR (q.queued_at = jobs.queued_at AND q.sequence <= jobs.sequence)))
        ELSE 0 END AS queue_position FROM jobs"""

    def get(self, job_id: str, owner: str) -> dict:
        with self._db() as db:
            row = db.execute(self._select + " WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if row is None:
            raise JobNotFound("Job not found.")
        return self._public(row)

    def list(self, owner: str, limit: int = 50) -> dict:
        with self._db() as db:
            rows = db.execute(self._select + " WHERE owner=? ORDER BY sequence DESC LIMIT ?", (owner, limit)).fetchall()
            counts = dict(db.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())
        return {"jobs": [self._public(row) for row in rows], "max_active": self.workers, "active": counts.get("processing", 0), "queued": counts.get("queued", 0)}

    def result(self, job_id: str, owner: str) -> str:
        with self._db() as db:
            row = db.execute("SELECT status, transcript FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
        if row is None:
            raise JobNotFound("Job not found.")
        if row["status"] != "completed":
            raise JobConflict("The transcript is not ready yet.")
        return row["transcript"]

    def retry(self, job_id: str, owner: str) -> dict:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status, input_path FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
            if row is None:
                raise JobNotFound("Job not found.")
            if row["status"] != "failed" or not Path(row["input_path"]).is_file():
                raise JobConflict("This job cannot be retried. Upload the recording again.")
            self._check_capacity(db)
            db.execute("UPDATE jobs SET status='queued', queued_at=?, started_at=NULL, finished_at=NULL, error=NULL, interrupted=0 WHERE id=?", (time.time(), job_id))
        self._wake.set()
        return self.get(job_id, owner)

    def delete(self, job_id: str, owner: str) -> None:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status, input_path FROM jobs WHERE id=? AND owner=?", (job_id, owner)).fetchone()
            if row is None:
                raise JobNotFound("Job not found.")
            if row["status"] == "processing":
                raise JobConflict("A processing job cannot be removed until it finishes.")
            db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        Path(row["input_path"]).unlink(missing_ok=True)

    def cleanup_failed_uploads(self) -> None:
        # Hold the write transaction so a retry cannot claim an expiring file.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT input_path FROM jobs WHERE status='completed' OR (status='failed' AND finished_at<?)", (time.time() - FAILED_UPLOAD_TTL,)).fetchall()
            for row in rows:
                Path(row["input_path"]).unlink(missing_ok=True)
