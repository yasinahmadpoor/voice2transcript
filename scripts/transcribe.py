"""Command-line transcription: uv run --locked python -m scripts.transcribe --help."""

import argparse
from pathlib import Path

from google import genai

from apps.transcriptions.services.pipeline import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    fail,
    require_binary,
    run_transcription_pipeline,
    write_outputs,
)
from common.environment import get_api_key_from_env, load_env_defaults


def main() -> None:
    parser = argparse.ArgumentParser(description="Gemini transcription + diarization")
    parser.add_argument(
        "input", help="Input audio/video file (any ffmpeg-supported format)"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name")
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
        default=DEFAULT_FALLBACK_MODEL,
        help="Fallback model used when primary model returns empty output",
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

    try:
        api_key = get_api_key_from_env()
    except RuntimeError as e:
        fail(str(e))

    out_prefix = (
        Path(args.output_prefix).expanduser().resolve()
        if args.output_prefix
        else input_path.with_suffix("")
    )

    client = genai.Client(api_key=api_key)

    result = run_transcription_pipeline(
        client=client,
        input_path=input_path,
        model=args.model,
        chunk_minutes=args.chunk_minutes,
        language_hint=args.language_hint,
        fallback_model=args.fallback_model,
    )
    write_outputs(out_prefix, result["language"], result["segments"])
    print("Done.")


if __name__ == "__main__":
    main()
