"""Transcription execution and persistence, independent of HTTP views."""

import os
from pathlib import Path
from uuid import uuid4

from google import genai

from apps.transcriptions.services.queue import JobNotFound
from common.environment import get_api_key_from_env
from common.http import APIError

from .pipeline import run_transcription_pipeline_raw


def run_pipeline(
    input_path: Path,
    model: str,
    chunk_minutes: float,
    language_hint: str,
    fallback_model: str,
    prompt: str,
) -> str:
    api_key = get_api_key_from_env()
    client = genai.Client(api_key=api_key)
    return run_transcription_pipeline_raw(
        client=client,
        input_path=input_path,
        model=model,
        chunk_minutes=chunk_minutes,
        language_hint=language_hint,
        fallback_model=fallback_model,
        prompt=prompt,
    )


def submit_recording(queue, username, upload, parameters, request_key=None):
    if request_key:
        existing = queue.find_request(username, request_key)
        if existing is not None:
            return existing
    queue.check_capacity()
    job_id = uuid4().hex
    input_path = queue.uploads / f"{job_id}.media"
    partial_path = input_path.with_suffix(".part")
    enqueued = False
    try:
        size = 0
        max_bytes = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
        with partial_path.open("wb") as destination:
            for chunk in upload.chunks(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise APIError(413, "File exceeds the upload size limit.")
                destination.write(chunk)
        if size == 0:
            raise APIError(400, "The uploaded file is empty.")
        partial_path.replace(input_path)
        job = queue.enqueue(
            job_id,
            username,
            Path(upload.name).name,
            input_path,
            parameters,
            request_key,
        )
        enqueued = True
        if job["id"] != job_id:
            # A simultaneous retry may have committed the same key first.
            input_path.unlink(missing_ok=True)
        return job
    finally:
        partial_path.unlink(missing_ok=True)
        if not enqueued:
            try:
                queue.get(job_id, username)
            except JobNotFound:
                input_path.unlink(missing_ok=True)
