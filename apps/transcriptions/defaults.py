"""Shared defaults for validation, API metadata, and transcription jobs."""

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
        "provider": str(provider.pk),
        "model": provider.model,
        "fallback_model": provider.fallback_model,
        "chunk_minutes": API_DEFAULT_CHUNK_MINUTES,
        "language_hint": API_DEFAULT_LANGUAGE_HINT,
        "prompt": API_DEFAULT_PROMPT,
    }
