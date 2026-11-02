"""Transcription execution and persistence, independent of HTTP views."""

import os
from pathlib import Path
from uuid import uuid4

from google import genai
from google.genai import types

from apps.transcriptions.services.queue import JobNotFound
from common.http import APIError

from .pipeline import run_transcription_pipeline_raw
from .execution import run_isolated
from .checkpoints import CheckpointMismatch, ChunkCheckpoint, current_checkpoint
from .providers import (
    ProviderError,
    resolve_provider,
    transcribe_gemini_inline,
    transcribe_openai,
)


def run_pipeline(
    input_path: Path,
    model: str,
    chunk_minutes: float,
    language_hint: str,
    fallback_model: str,
    prompt: str,
    provider: str = "env",
) -> str:
    return run_isolated(
        {
            "input_path": str(input_path),
            "model": model,
            "chunk_minutes": chunk_minutes,
            "language_hint": language_hint,
            "fallback_model": fallback_model,
            "prompt": prompt,
            "provider": provider,
            "checkpoint_context": current_checkpoint(),
        }
    )


def execute_pipeline(
    input_path: Path,
    model: str,
    chunk_minutes: float,
    language_hint: str,
    fallback_model: str,
    prompt: str,
    provider: str = "env",
    checkpoint_context=None,
) -> str:
    try:
        selected = resolve_provider(provider)
        checkpoint = (
            ChunkCheckpoint(
                checkpoint_context,
                input_path,
                {
                    "provider": provider,
                    "kind": selected.kind,
                    "base_url": selected.base_url.rstrip("/"),
                    "model": model,
                    "fallback_model": fallback_model,
                    "chunk_minutes": chunk_minutes,
                    "language_hint": language_hint,
                    "prompt": prompt,
                },
            )
            if checkpoint_context
            else None
        )
        options = {"checkpoint": checkpoint} if checkpoint is not None else {}
        if selected.kind == "openai":
            return transcribe_openai(
                selected,
                input_path,
                model,
                chunk_minutes,
                language_hint,
                fallback_model,
                prompt,
                **options,
            )
        if selected.base_url:
            api_key = selected.get_api_key()
            if not api_key:
                raise ProviderError(
                    "Add the custom Gemini provider's API key in admin."
                )
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(
                    base_url=selected.base_url, api_version="v1beta", timeout=600_000
                ),
            )
            try:
                return transcribe_gemini_inline(
                    client,
                    input_path,
                    model,
                    chunk_minutes,
                    language_hint,
                    fallback_model,
                    prompt,
                    **options,
                )
            finally:
                client.close()
        api_key = selected.get_api_key()
        if not api_key:
            raise ProviderError("Add this Gemini provider's API key in admin.")
        client = genai.Client(api_key=api_key)
        try:
            return run_transcription_pipeline_raw(
                client=client,
                input_path=input_path,
                model=model,
                chunk_minutes=chunk_minutes,
                language_hint=language_hint,
                fallback_model=fallback_model,
                prompt=prompt,
                **options,
            )
        finally:
            client.close()
    except (ProviderError, CheckpointMismatch):
        raise
    except Exception:
        # SDK exceptions can contain credentials, URLs, or response contents.
        raise ProviderError(
            "Transcription provider failed. Check the selected provider in admin."
        ) from None


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
