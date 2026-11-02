"""Small, bounded admin probes using the production audio adapters."""

import json
import re
import time
from pathlib import Path

import httpx
from google import genai
from google.genai import errors, types

from .execution import run_isolated
from .pipeline import run_transcription_pipeline_raw
from .providers import ProviderError, transcribe_gemini_inline, transcribe_openai

TEST_AUDIO = Path(__file__).resolve().parents[1] / "fixtures/connection-test.wav"
TEST_PROMPT = (
    "Transcribe the spoken words in this recording. Return only the transcript."
)
TEST_TIMEOUT_SECONDS = 30
_MESSAGES = {
    400: "The provider rejected the audio request. Check the model and audio API compatibility.",
    401: "Authentication failed. Check the saved API key.",
    403: "Access denied. Check the API key permissions and model access.",
    404: "The endpoint or model was not found. Check the base URL and model name.",
    402: "The provider requires payment or additional credits.",
    429: "The provider rate limit or quota was reached. Check your credits or try again later.",
}


def status_message(code):
    if code in _MESSAGES:
        return _MESSAGES[code]
    if 500 <= code <= 599:
        return "The provider is temporarily unavailable. Try again later."
    return "The provider rejected the request. Check its configuration and audio API support."


def execute_connection_test(provider_id):
    """Run in a private subprocess; return only fixed, credential-safe diagnostics."""
    from ..models import ModelProvider

    try:
        # Disabled providers can be tested before enabling them. No job is created.
        provider = ModelProvider.objects.get(pk=provider_id)
        options = {
            "input_path": TEST_AUDIO,
            "model": provider.model,
            "chunk_minutes": 0,
            "language_hint": "en",
            "fallback_model": "",
            "prompt": TEST_PROMPT,
        }
        if provider.kind == ModelProvider.Kind.OPENAI:
            text = transcribe_openai(provider, **options, request_timeout=15)
        else:
            key = provider.get_api_key()
            if not key:
                return {
                    "ok": False,
                    "message": "Add this Gemini provider's API key before testing.",
                }
            http_options = types.HttpOptions(
                timeout=15_000,
                retry_options=types.HttpRetryOptions(attempts=1),
                **(
                    {"base_url": provider.base_url, "api_version": "v1beta"}
                    if provider.base_url
                    else {}
                ),
            )
            client = genai.Client(api_key=key, http_options=http_options)
            try:
                adapter = (
                    transcribe_gemini_inline
                    if provider.base_url
                    else run_transcription_pipeline_raw
                )
                text = adapter(client=client, **options)
            finally:
                client.close()
        if not isinstance(text, str) or not text.strip():
            return {
                "ok": False,
                "message": "The model returned no transcript. Check that it supports audio transcription.",
            }
        return {
            "ok": True,
            "message": "Connection successful. The saved primary model returned a transcript for the test recording.",
        }
    except (httpx.TimeoutException, TimeoutError):
        message = "The provider timed out. Try again or check the endpoint."
    except httpx.RequestError:
        message = "Could not connect to the provider. Check its URL, network, and TLS certificate."
    except errors.APIError as exc:
        message = (
            status_message(exc.code)
            if isinstance(exc.code, int)
            else "Gemini rejected the request. Check the API key, model, and endpoint."
        )
    except ProviderError as exc:
        # Adapter errors are deliberately safe. Never return SDK bodies or URLs.
        match = re.fullmatch(
            r"Provider returned HTTP ([0-9]{3})\. Check its configuration\.", str(exc)
        )
        message = (
            status_message(int(match[1]))
            if match
            else "The provider returned an invalid or empty transcript. Check audio API compatibility."
        )
    except Exception:
        # This includes unreadable saved credentials and native SDK failures.
        message = "The model test failed. Check the saved key, endpoint, model, and audio support."
    return {"ok": False, "message": message}


def check_connection(provider):
    started = time.monotonic()
    try:
        # Credentials remain in the database; the child receives only a provider ID.
        result = json.loads(
            run_isolated(
                {"connection_test_provider": str(provider.pk)},
                timeout_seconds=TEST_TIMEOUT_SECONDS,
            )
        )
        if (
            not isinstance(result, dict)
            or type(result.get("ok")) is not bool
            or not isinstance(result.get("message"), str)
        ):
            raise ValueError("Invalid probe result")
    except TimeoutError:
        result = {
            "ok": False,
            "message": "The connection test timed out after 30 seconds. Check the provider or try again.",
        }
    except Exception:
        result = {
            "ok": False,
            "message": "The connection test could not finish. Check the server and provider configuration.",
        }
    return result | {"elapsed_seconds": round(time.monotonic() - started, 2)}
