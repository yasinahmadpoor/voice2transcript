"""Provider lookup and adapters for custom Gemini and OpenAI-compatible audio."""

import json
import re
import tempfile
from pathlib import Path

import httpx
from google.genai import types

from common.http import APIError
from .checkpoints import transcribe_chunks

from .pipeline import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    PROMPT_BASE,
    convert_to_wav,
    split_wav,
    generate_transcription_response_raw,
    extract_response_text_exact,
)


def resolve_provider(value=None):
    from ..models import ModelProvider

    if value is None:
        return ModelProvider.objects.filter(is_default=True, is_enabled=True).first()
    if value == "env":
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,18}", value):
        raise APIError(422, "Choose an enabled transcription provider.")
    provider = ModelProvider.objects.filter(pk=int(value), is_enabled=True).first()
    if provider is None:
        raise APIError(422, "This transcription provider is unavailable.")
    return provider


def available_providers():
    from ..models import ModelProvider

    return [
        {
            "id": "env",
            "name": "Gemini (.env)",
            "kind": "gemini",
            "model": DEFAULT_MODEL,
            "fallback_model": DEFAULT_FALLBACK_MODEL,
        },
        *[
            {
                "id": str(item.pk),
                "name": item.name,
                "kind": item.kind,
                "model": item.model,
                "fallback_model": item.fallback_model,
            }
            for item in ModelProvider.objects.filter(is_enabled=True)
        ],
    ]


class ProviderError(RuntimeError):
    """Safe diagnostic that never includes credentials or provider response bodies."""


def transcribe_gemini_inline(
    client,
    input_path,
    model,
    chunk_minutes,
    language_hint,
    fallback_model,
    prompt,
    checkpoint=None,
):
    """Use generateContent alone so Gemini proxies need no Files API support."""
    effective_prompt = prompt.strip() or PROMPT_BASE
    if language_hint.strip():
        effective_prompt += (
            f"\nPreferred spoken language hint: {language_hint.strip()}."
        )
    minutes = min(chunk_minutes, 5.0) if chunk_minutes > 0 else 0
    with tempfile.TemporaryDirectory(prefix="gemini_proxy_transcription_") as directory:
        wav = Path(directory) / "recording.wav"
        convert_to_wav(input_path, wav)
        chunks = split_wav(wav, minutes)

        def transcribe(path):
            size = path.stat().st_size
            if size > 10 * 1024 * 1024:
                raise ProviderError(
                    "Gemini proxy audio exceeds 10 MB. Enable chunking or use shorter chunks."
                )
            encoded_size = (
                4 * ((size + 2) // 3)
                + len(json.dumps(effective_prompt).encode())
                + 4096
            )
            if encoded_size > 20 * 1024 * 1024:
                raise ProviderError(
                    "Gemini proxy audio and prompt exceed the inline request limit. Use a shorter prompt or smaller chunks."
                )
            audio = types.Part.from_bytes(data=path.read_bytes(), mime_type="audio/wav")
            response = generate_transcription_response_raw(
                client, model, effective_prompt, audio
            )
            text = extract_response_text_exact(response)
            if not text.strip() and fallback_model and fallback_model != model:
                response = generate_transcription_response_raw(
                    client, fallback_model, effective_prompt, audio
                )
                text = extract_response_text_exact(response)
            if not text.strip():
                raise ProviderError("Gemini proxy returned no transcript text.")
            return text

        return transcribe_chunks(chunks, transcribe, checkpoint)


def _request_chunk(client, provider, path, model, language_hint, prompt):
    data = {"model": model, "response_format": "json"}
    if prompt:
        data["prompt"] = prompt
    if re.fullmatch(r"[a-zA-Z]{2}", language_hint):
        data["language"] = language_hint.lower()
    elif language_hint:
        data["prompt"] = f"{prompt}\nPreferred spoken language: {language_hint}."
    with path.open("rb") as audio:
        response = client.post(
            provider.base_url.rstrip("/") + "/audio/transcriptions",
            data=data,
            files={"file": (path.name, audio, "audio/wav")},
        )
    if not response.is_success:
        raise ProviderError(
            f"Provider returned HTTP {response.status_code}. Check its configuration."
        )
    try:
        body = response.json()
    except ValueError:
        raise ProviderError("Provider returned invalid JSON.") from None
    if not isinstance(body, dict):
        raise ProviderError("Provider returned an unsupported response.")
    text = body.get("text", "")
    return text if isinstance(text, str) else ""


def transcribe_openai(
    provider,
    input_path,
    model,
    chunk_minutes,
    language_hint,
    fallback_model,
    prompt,
    checkpoint=None,
):
    key = provider.get_api_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # Ten minutes of normalized 16 kHz mono PCM stays below 25 MB.
    minutes = min(chunk_minutes, 10.0) if chunk_minutes > 0 else 0
    with tempfile.TemporaryDirectory(prefix="compatible_transcription_") as directory:
        wav = Path(directory) / "recording.wav"
        convert_to_wav(input_path, wav)
        chunks = split_wav(wav, minutes)
        with httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(600, connect=30),
            follow_redirects=False,
        ) as client:

            def transcribe(path):
                if path.stat().st_size > 25 * 1024 * 1024:
                    raise ProviderError(
                        "Audio chunk exceeds 25 MB. Enable chunking for this recording."
                    )
                text = _request_chunk(
                    client, provider, path, model, language_hint, prompt
                )
                if not text.strip() and fallback_model and fallback_model != model:
                    text = _request_chunk(
                        client, provider, path, fallback_model, language_hint, prompt
                    )
                if not text.strip():
                    raise ProviderError("Provider returned no transcript text.")
                return text

            return transcribe_chunks(chunks, transcribe, checkpoint)
