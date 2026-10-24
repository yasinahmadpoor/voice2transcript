#!/usr/bin/env python3

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from google import genai
from starlette.concurrency import run_in_threadpool

from transcription_diarize import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    get_api_key_from_env,
    load_env_defaults,
    require_binary,
    run_transcription_pipeline_raw,
)


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


app = FastAPI(title="Gemini Transcription API", version="1.0.0")


def _run_pipeline(
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


@app.on_event("startup")
def _startup_checks() -> None:
    load_env_defaults()
    require_binary("ffmpeg")
    require_binary("ffprobe")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/transcribe", response_class=PlainTextResponse)
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    chunk_minutes: float = Form(20.0),
    language_hint: str = Form("fa"),
    fallback_model: str = Form(DEFAULT_FALLBACK_MODEL),
    prompt: str = Form(API_DEFAULT_PROMPT),
) -> PlainTextResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    suffix = Path(file.filename).suffix or ".bin"
    tmp_dir = Path("/tmp/opencode") / f"upload_{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    input_path = tmp_dir / f"input{suffix}"

    try:
        with input_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
        await file.close()

        txt_output = await run_in_threadpool(
            _run_pipeline,
            input_path,
            model,
            chunk_minutes,
            language_hint,
            fallback_model,
            prompt,
        )

        return PlainTextResponse(
            content=txt_output,
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="transcription.txt"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    finally:
        try:
            if input_path.exists():
                input_path.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()
        except Exception:
            pass
