"""Shared defaults for validation, API metadata, and transcription jobs."""

from apps.transcriptions.services.pipeline import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL

API_DEFAULT_PROMPT = """You are an expert transcription and diarization engine.

Tasks:
1) Transcribe spoken content accurately.
2) Diarize speakers as SPEAKER_1, SPEAKER_2, ...
3) Keep strict chronological order.

Return plain text only in this exact style:
SPEAKER_1: text
SPEAKER_2: text

Rules:
- No timestamps.
- No JSON.
- No markdown fences.
- No extra explanations.
"""
API_DEFAULT_CHUNK_MINUTES = 20.0
API_DEFAULT_LANGUAGE_HINT = "fa"


def transcription_defaults(selected=None):
    from .services.providers import resolve_provider

    provider = resolve_provider(selected)
    return {
        "provider": str(provider.pk) if provider else "env",
        "model": provider.model if provider else DEFAULT_MODEL,
        "fallback_model": provider.fallback_model
        if provider
        else DEFAULT_FALLBACK_MODEL,
        "chunk_minutes": API_DEFAULT_CHUNK_MINUTES,
        "language_hint": API_DEFAULT_LANGUAGE_HINT,
        "prompt": API_DEFAULT_PROMPT,
    }
