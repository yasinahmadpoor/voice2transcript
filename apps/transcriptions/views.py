"""HTTP views for recording uploads, job history, and transcripts."""

import time
import json
import unicodedata

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils.http import content_disposition_header

from common.http import APIError, methods, validated

from .defaults import API_DEFAULT_PROMPT, transcription_defaults
from .forms import PromptContextForm, TranscriptionForm, request_key
from .services.prompts import compose_prompt
from .services.jobs import submit_recording
from .services.runtime import get_queue


@methods("GET")
def frontend(request):
    return render(request, "transcriptions/index.html")


@methods("GET")
def defaults(request):
    return JsonResponse(transcription_defaults())


@methods("GET")
def providers(request):
    from .services.providers import available_providers

    return JsonResponse({"providers": available_providers()})


@methods("GET")
def health(request):
    return JsonResponse({"ok": True})


@methods("POST")
def prompt_preview(request):
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise APIError(
            422, "Expected JSON with prompt, meeting_description, and participants."
        ) from None
    if (
        not isinstance(data, dict)
        or set(data) - {"prompt", "meeting_description", "participants"}
        or not isinstance(data.get("prompt", ""), str)
        or not isinstance(data.get("meeting_description", ""), str)
        or not isinstance(data.get("participants", []), list)
    ):
        raise APIError(
            422,
            "Expected text prompt and meeting_description fields and a participants array.",
        )
    parameters = {
        "prompt": API_DEFAULT_PROMPT,
        "meeting_description": "",
        "participants": [],
    } | data
    if not parameters["prompt"]:
        parameters["prompt"] = API_DEFAULT_PROMPT
    fields = validated(PromptContextForm(parameters))
    return JsonResponse({"effective_prompt": compose_prompt(**fields)})


@methods("GET", "POST")
def jobs(request):
    if request.method == "POST":
        return transcribe(request)
    try:
        limit = int(request.GET.get("limit", "50"))
    except ValueError as exc:
        raise APIError(422, "Limit must be an integer between 1 and 100.") from exc
    if not 1 <= limit <= 100:
        raise APIError(422, "Limit must be between 1 and 100.")
    return JsonResponse(get_queue().list(request.account, limit))


@methods("GET", "DELETE")
def job_detail(request, job_id):
    queue = get_queue()
    if request.method == "DELETE":
        queue.delete(job_id, request.account)
        return HttpResponse(status=204)
    return JsonResponse(queue.get(job_id, request.account))


def transcript_response(text):
    return HttpResponse(
        text,
        content_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="transcription.txt"'},
    )


@methods("GET")
def job_result(request, job_id):
    return transcript_response(get_queue().result(job_id, request.account))


@methods("POST")
def job_export(request, job_id):
    from .services.exports import (
        EXPORT_TYPES,
        MAX_EXPORT_CHARACTERS,
        export_filename,
        generate_export,
    )

    queue = get_queue()
    job = queue.get(job_id, request.account)
    if job["status"] != "completed":
        raise APIError(409, "The transcript is not ready yet.")
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise APIError(422, "Expected JSON with format and optional text.") from None
    if (
        not isinstance(data, dict)
        or set(data) - {"format", "text"}
        or data.get("format") not in ("docx", "pdf")
        or ("text" in data and not isinstance(data["text"], str))
    ):
        raise APIError(422, "Choose docx or pdf and provide text as a string.")
    text = data["text"] if "text" in data else queue.result(job_id, request.account)
    if not text.strip():
        raise APIError(422, "The transcript is empty.")
    if len(text) > MAX_EXPORT_CHARACTERS:
        raise APIError(
            422,
            f"Word and PDF exports support up to {MAX_EXPORT_CHARACTERS:,} characters. Download TXT for longer transcripts.",
        )
    if any(
        unicodedata.category(char) == "Cs"
        or (ord(char) < 32 and char not in "\n\r\t")
        or ord(char) in (0xFFFE, 0xFFFF)
        for char in text
    ):
        raise APIError(422, "The transcript contains unsupported control characters.")
    format = data["format"]
    document = generate_export(
        format, text, job["filename"], job["finished_at"] or job["created_at"]
    )
    return HttpResponse(
        document,
        content_type=EXPORT_TYPES[format],
        headers={
            "Content-Disposition": content_disposition_header(
                True, export_filename(job["filename"], format)
            ),
        },
    )


@methods("GET")
def job_context(request, job_id):
    return JsonResponse(get_queue().context(job_id, request.account))


@methods("POST")
def retry_job(request, job_id):
    restart = False
    if request.content_type == "multipart/form-data":
        # Existing clients may POST an empty multipart form to retry.
        if request.POST or request.FILES:
            raise APIError(422, "Expected JSON with an optional boolean restart field.")
    elif request.body:
        try:
            options = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise APIError(
                422, "Expected JSON with an optional boolean restart field."
            ) from None
        if (
            not isinstance(options, dict)
            or set(options) - {"restart"}
            or type(options.get("restart", False)) is not bool
        ):
            raise APIError(422, "Expected JSON with an optional boolean restart field.")
        restart = options.get("restart", False)
    return JsonResponse(
        get_queue().retry(job_id, request.account, restart=restart), status=202
    )


@methods("POST")
def cancel_job(request, job_id):
    return JsonResponse(get_queue().cancel(job_id, request.account), status=202)


@methods("POST")
def transcribe(request):
    key = request_key(request)
    queue = get_queue()
    # Look up retries and check capacity before parsing any multipart data.
    job = queue.find_request(request.account, key) if key else None
    if job is None:
        queue.check_capacity()
        parameters = validated(TranscriptionForm(request.POST, request.FILES))
        upload = parameters.pop("file")
        try:
            job = submit_recording(queue, request.account, upload, parameters, key)
        finally:
            upload.close()
    if request.path.rstrip("/") == "/jobs":
        return JsonResponse(job, status=202, headers={"Location": f"/jobs/{job['id']}"})
    while job["status"] in {"queued", "processing", "cancelling"}:
        time.sleep(0.25)
        job = queue.get(job["id"], request.account)
    if job["status"] == "cancelled":
        raise APIError(409, "Transcription was cancelled.")
    if job["status"] != "completed":
        raise APIError(500, job["error"])
    return transcript_response(queue.result(job["id"], request.account))
