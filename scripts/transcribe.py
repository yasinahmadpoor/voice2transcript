"""Command-line transcription: uv run --locked python -m scripts.transcribe --help."""

import argparse
import os
from pathlib import Path

from google import genai
from google.genai import types

from apps.transcriptions.services.pipeline import (
    fail,
    require_binary,
    run_transcription_pipeline,
    write_outputs,
)
from common.environment import load_env_defaults


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini transcription + diarization")
    parser.add_argument(
        "input", help="Input audio/video file (any ffmpeg-supported format)"
    )
    parser.add_argument(
        "--model", default=None, help="Override the saved Gemini model name"
    )
    parser.add_argument(
        "--chunk-minutes",
        type=float,
        default=20.0,
        help="Chunk length in minutes (0 to disable splitting)",
    )
    parser.add_argument(
        "--language-hint", default="fa", help="Optional language hint, e.g., en, fa"
    )
    parser.add_argument(
        "--fallback-model",
        default=None,
        help="Override the saved Gemini fallback model; pass an empty string to disable it",
    )
    parser.add_argument(
        "--output-prefix",
        default="",
        help="Output prefix path (default: input filename without extension)",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        fail(f"Input file not found: {input_path}")

    require_binary("ffmpeg")
    require_binary("ffprobe")
    load_env_defaults()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
    import django

    django.setup()
    from django.db import DatabaseError
    from apps.transcriptions.services.providers import resolve_provider
    from common.http import APIError

    try:
        provider = resolve_provider("env")
        if provider.kind != "gemini":
            fail("The CLI requires the seeded provider to be a Gemini provider.")
        api_key = provider.get_api_key()
        if not api_key:
            fail("Add the Gemini provider's API key in admin.")
    except (DatabaseError, APIError):
        fail(
            "Run manage.py migrate and manage.py seed_providers, then enable the saved Gemini provider in admin."
        )
    except RuntimeError:
        fail("The saved Gemini key cannot be decrypted. Re-enter it in admin.")

    out_prefix = (
        Path(args.output_prefix).expanduser().resolve()
        if args.output_prefix
        else input_path.with_suffix("")
    )

    client = genai.Client(
        api_key=api_key,
        **(
            {
                "http_options": types.HttpOptions(
                    base_url=provider.base_url, api_version="v1beta"
                )
            }
            if provider.base_url
            else {}
        ),
    )
    try:
        result = run_transcription_pipeline(
            client=client,
            input_path=input_path,
            model=args.model if args.model is not None else provider.model,
            chunk_minutes=args.chunk_minutes,
            language_hint=args.language_hint,
            fallback_model=args.fallback_model
            if args.fallback_model is not None
            else provider.fallback_model,
        )
    finally:
        client.close()
    write_outputs(out_prefix, result["language"], result["segments"])
    print("Done.")


if __name__ == "__main__":
    main()
