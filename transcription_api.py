#!/usr/bin/env python3

import logging
import os
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from google import genai
from starlette.concurrency import run_in_threadpool

from auth import COOKIE_NAME, SESSION_SECONDS, check_login_rate, create_session, delete_session, require_user, verify_credentials

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
API_DEFAULT_CHUNK_MINUTES = 20.0
API_DEFAULT_LANGUAGE_HINT = "fa"


app = FastAPI(title="Gemini Transcription API", version="1.0.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
logger = logging.getLogger(__name__)


@app.middleware("http")
async def protect_requests(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
    # Authenticate before parsing a potentially large multipart upload.
    if request.url.path.rstrip("/") == "/transcribe":
        try:
            require_user(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@app.post("/auth/login")
def login(credentials: LoginInput, request: Request, response: Response) -> dict:
    check_login_rate(request)
    if not verify_credentials(credentials.username, credentials.password):
        raise HTTPException(401, "Incorrect username or password.")
    delete_session(request)
    response.set_cookie(
        COOKIE_NAME, create_session(), max_age=SESSION_SECONDS, httponly=True,
        secure=os.getenv("SECURE_COOKIES", "false").lower() == "true", samesite="strict",
    )
    return {"username": credentials.username}


@app.get("/auth/me")
def current_user(username: str = Depends(require_user)) -> dict:
    return {"username": username, "max_upload_mb": int(os.getenv("MAX_UPLOAD_MB", "500"))}


@app.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    delete_session(request)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, httponly=True, samesite="strict")
    return response


@app.get("/transcription/defaults")
def transcription_defaults(username: str = Depends(require_user)) -> dict:
    return {
        "model": DEFAULT_MODEL,
        "fallback_model": DEFAULT_FALLBACK_MODEL,
        "chunk_minutes": API_DEFAULT_CHUNK_MINUTES,
        "language_hint": API_DEFAULT_LANGUAGE_HINT,
        "prompt": API_DEFAULT_PROMPT,
    }


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
    request: Request,
    username: str = Depends(require_user),
    file: UploadFile = File(...),
    model: str = Form(DEFAULT_MODEL),
    chunk_minutes: float = Form(API_DEFAULT_CHUNK_MINUTES, ge=0, le=120),
    language_hint: str = Form(API_DEFAULT_LANGUAGE_HINT, max_length=100),
    fallback_model: str = Form(DEFAULT_FALLBACK_MODEL),
    prompt: str = Form(API_DEFAULT_PROMPT),
) -> PlainTextResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    # FastAPI replaces blank optional form fields with their defaults. Preserve
    # explicit blanks for auto-detection and disabling the fallback model.
    submitted_form = await request.form()
    if submitted_form.get("language_hint") == "":
        language_hint = ""
    if submitted_form.get("fallback_model") == "":
        fallback_model = ""

    suffix = Path(file.filename).suffix or ".bin"
    tmp_dir = Path("/tmp/opencode") / f"upload_{uuid4().hex}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    input_path = tmp_dir / f"input{suffix}"

    try:
        size = 0
        max_bytes = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
        with input_path.open("wb") as f:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, "File exceeds the upload size limit.")
                f.write(chunk)
        if size == 0:
            raise HTTPException(400, "The uploaded file is empty.")

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
        logger.exception("Transcription failed")
        raise HTTPException(status_code=500, detail="Transcription failed. Check that the file contains playable audio and try again. If it continues, check the server logs and Gemini API configuration.") from e
    finally:
        await file.close()
        try:
            if input_path.exists():
                input_path.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()
        except Exception:
            pass
