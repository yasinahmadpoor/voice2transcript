#!/usr/bin/env python3

import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from google import genai
from starlette.concurrency import run_in_threadpool

from auth import COOKIE_NAME, SESSION_SECONDS, check_login_rate, create_session, delete_session, require_user, verify_credentials
from job_queue import JobConflict, JobNotFound, JobQueue, QueueFull

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_env_defaults()
    require_binary("ffmpeg")
    require_binary("ffprobe")
    queue = JobQueue(
        Path(os.getenv("JOB_DATA_DIR", str(Path(__file__).resolve().parent / "data"))).resolve(),
        lambda *args: _run_pipeline(*args),
        workers=int(os.getenv("TRANSCRIPTION_WORKERS", "2")),
        max_pending=int(os.getenv("MAX_PENDING_JOBS", "20")),
    )
    await run_in_threadpool(queue.start)
    app.state.jobs = queue
    try:
        yield
    finally:
        await run_in_threadpool(queue.stop)


app = FastAPI(title="Gemini Transcription API", version="1.0.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(JobNotFound)
async def job_not_found(request: Request, exc: JobNotFound):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(JobConflict)
async def job_conflict(request: Request, exc: JobConflict):
    return JSONResponse({"detail": str(exc)}, status_code=409)


@app.exception_handler(QueueFull)
async def queue_full(request: Request, exc: QueueFull):
    return JSONResponse({"detail": str(exc)}, status_code=429, headers={"Retry-After": "60"})


@app.middleware("http")
async def protect_requests(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
    # Authenticate before parsing a potentially large multipart upload.
    if request.url.path.rstrip("/") in {"/transcribe", "/jobs"} or request.url.path.startswith("/jobs/"):
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


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/jobs")
def list_jobs(request: Request, username: str = Depends(require_user), limit: int = Query(50, ge=1, le=100)) -> dict:
    return request.app.state.jobs.list(username, limit)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, username: str = Depends(require_user)) -> dict:
    return request.app.state.jobs.get(job_id, username)


@app.get("/jobs/{job_id}/result", response_class=PlainTextResponse)
def job_result(job_id: str, request: Request, username: str = Depends(require_user)) -> PlainTextResponse:
    return PlainTextResponse(request.app.state.jobs.result(job_id, username), headers={"Content-Disposition": 'attachment; filename="transcription.txt"'})


@app.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, request: Request, username: str = Depends(require_user)) -> dict:
    return request.app.state.jobs.retry(job_id, username)


@app.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, request: Request, username: str = Depends(require_user)) -> Response:
    request.app.state.jobs.delete(job_id, username)
    return Response(status_code=204)


async def respond_to_job(job: dict, request: Request, username: str) -> Response:
    job_id = job["id"]
    if request.url.path.rstrip("/") == "/jobs":
        return JSONResponse(job, status_code=202, headers={"Location": f"/jobs/{job_id}"})
    queue = request.app.state.jobs
    # Compatibility endpoint: work uses the same persistent queue.
    while job["status"] in {"queued", "processing"}:
        await asyncio.sleep(0.25)
        job = await run_in_threadpool(queue.get, job_id, username)
    if job["status"] != "completed":
        raise HTTPException(500, job["error"])
    return PlainTextResponse(
        content=await run_in_threadpool(queue.result, job_id, username),
        headers={"Content-Disposition": 'attachment; filename="transcription.txt"'},
    )


@app.post("/jobs", status_code=202)
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
    idempotency_key: str | None = Header(None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
) -> Response:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Uploaded file must have a filename.")

    # FastAPI replaces blank optional form fields with their defaults. Preserve
    # explicit blanks for auto-detection and disabling the fallback model.
    submitted_form = await request.form()
    if submitted_form.get("language_hint") == "":
        language_hint = ""
    if submitted_form.get("fallback_model") == "":
        fallback_model = ""

    queue = request.app.state.jobs
    if idempotency_key:
        existing = await run_in_threadpool(queue.find_request, username, idempotency_key)
        if existing is not None:
            await file.close()
            return await respond_to_job(existing, request, username)
    await run_in_threadpool(queue.check_capacity)
    job_id = uuid4().hex
    # ffmpeg detects the format from content; never use a user-supplied path.
    input_path = queue.uploads / f"{job_id}.media"
    partial_path = input_path.with_suffix(".part")
    enqueued = False
    try:
        size = 0
        max_bytes = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
        with partial_path.open("wb") as f:
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
        partial_path.replace(input_path)
        job = await run_in_threadpool(
            queue.enqueue, job_id, username, Path(file.filename).name, input_path,
            {"model": model, "chunk_minutes": chunk_minutes, "language_hint": language_hint, "fallback_model": fallback_model, "prompt": prompt},
            idempotency_key,
        )
        enqueued = True
        if job["id"] != job_id:
            # Another request with the same retry key committed first.
            input_path.unlink(missing_ok=True)
        return await respond_to_job(job, request, username)
    finally:
        await file.close()
        partial_path.unlink(missing_ok=True)
        if not enqueued:
            # A cancelled HTTP request may have committed its job already.
            # Never remove an upload that belongs to a persisted queue entry.
            try:
                queue.get(job_id, username)
            except JobNotFound:
                input_path.unlink(missing_ok=True)
