"""Durable chunk results, shared by all providers and supervised job attempts."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar

from .execution import JobCancelled

SCHEMA = """CREATE TABLE IF NOT EXISTS job_checkpoints (
    job_id varchar(32) NOT NULL PRIMARY KEY,
    fingerprint varchar(64) NOT NULL,
    plan text NOT NULL,
    completed text NOT NULL
)"""
_context = ContextVar("checkpoint_context", default=None)


class CheckpointMismatch(Exception):
    MESSAGE = "Saved progress no longer matches this recording or provider configuration. Choose Restart from beginning."

    def __init__(self):
        super().__init__(self.MESSAGE)


@contextmanager
def checkpoint_scope(database, job_id, started_at):
    token = _context.set(
        {"database": str(database), "job_id": job_id, "started_at": started_at}
    )
    try:
        yield
    finally:
        _context.reset(token)


def current_checkpoint():
    return _context.get()


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ChunkCheckpoint:
    def __init__(self, context, input_path, configuration):
        self.context = context
        payload = {"version": 1, "recording": file_digest(input_path), **configuration}
        self.fingerprint = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()
        self.completed = {}

    @contextmanager
    def transaction(self):
        connection = sqlite3.connect(self.context["database"], timeout=10)
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                job = connection.execute(
                    "SELECT status, started_at FROM jobs WHERE id=?",
                    (self.context["job_id"],),
                ).fetchone()
                if job is None or job != ("processing", self.context["started_at"]):
                    raise JobCancelled()
                yield connection
        finally:
            connection.close()

    def prepare(self, chunks):
        plan = [
            {"offset": offset, "sha256": file_digest(path)} for path, offset in chunks
        ]
        with self.transaction() as db:
            row = db.execute(
                "SELECT fingerprint, plan, completed FROM job_checkpoints WHERE job_id=?",
                (self.context["job_id"],),
            ).fetchone()
            if row is not None:
                try:
                    previous_plan, completed = json.loads(row[1]), json.loads(row[2])
                    valid = isinstance(completed, dict) and all(
                        key.isdecimal()
                        and str(int(key)) == key
                        and 0 <= int(key) < len(plan)
                        and isinstance(text, str)
                        and text.strip()
                        for key, text in completed.items()
                    )
                except (ValueError, TypeError, AttributeError):
                    raise CheckpointMismatch() from None
                if not valid or (
                    completed and (row[0] != self.fingerprint or previous_plan != plan)
                ):
                    raise CheckpointMismatch()
                if row[0] == self.fingerprint and previous_plan == plan:
                    self.completed = completed
                    return
            db.execute(
                "INSERT OR REPLACE INTO job_checkpoints (job_id,fingerprint,plan,completed) VALUES (?,?,?,?)",
                (self.context["job_id"], self.fingerprint, json.dumps(plan), "{}"),
            )

    def save(self, index, text):
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Transcription returned no text.")
        completed = self.completed | {str(index): text}
        with self.transaction() as db:
            updated = db.execute(
                "UPDATE job_checkpoints SET completed=? WHERE job_id=? AND fingerprint=?",
                (
                    json.dumps(completed, ensure_ascii=False),
                    self.context["job_id"],
                    self.fingerprint,
                ),
            )
            if updated.rowcount != 1:
                raise CheckpointMismatch()
        self.completed = completed


def transcribe_chunks(chunks, transcribe, checkpoint=None):
    if checkpoint is not None:
        checkpoint.prepare(chunks)
    results = []
    for index, (path, _offset) in enumerate(chunks):
        if checkpoint is not None:
            with checkpoint.transaction():
                pass
        text = checkpoint.completed.get(str(index)) if checkpoint is not None else None
        if text is None:
            text = transcribe(path)
            if checkpoint is not None:
                checkpoint.save(index, text)
        results.append(text)
    return "\n\n".join(results)
