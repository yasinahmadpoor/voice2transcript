"""Authenticate before parsing uploads and preserve the browser's security policy."""

from urllib.parse import urlsplit

from django.core.exceptions import RequestDataTooBig, SuspiciousOperation
from django.http import JsonResponse
from django.http.multipartparser import MultiPartParserError

from apps.accounts.services import owner_key, require_user
from apps.transcriptions.services.queue import JobConflict, JobNotFound, QueueFull

from .http import APIError, error_response


class RequestProtectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Validate Host for every request, including those served by WhiteNoise.
        request.get_host()
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                expected = f"{request.scheme}://{request.get_host()}"
                origin = request.headers.get("Origin")
                referer = request.headers.get("Referer")
                if origin is not None and origin != expected:
                    raise APIError(403, "Cross-site requests are not allowed.")
                if request.headers.get("Sec-Fetch-Site") == "cross-site":
                    raise APIError(403, "Cross-site requests are not allowed.")
                if origin is None and referer:
                    parsed = urlsplit(referer)
                    if f"{parsed.scheme}://{parsed.netloc}" != expected:
                        raise APIError(403, "Cross-site requests are not allowed.")
            response = self.get_response(request)
        except APIError as exc:
            response = error_response(exc)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.path.startswith("/admin/"):
            # Django admin widgets use inline style attributes for positioning.
            response.headers["Content-Security-Policy"] = response.headers[
                "Content-Security-Policy"
            ].replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")
        response.headers["Cache-Control"] = "no-store"
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        # AuthenticationMiddleware has now populated request.user. This hook
        # still runs before CSRF/view code can parse a multipart upload.
        path = request.path.rstrip("/")
        if path in {
            "/transcribe",
            "/jobs",
            "/auth/me",
            "/transcription/defaults",
            "/transcription/providers",
            "/transcription/prompt-preview",
        } or path.startswith("/jobs/"):
            try:
                request.account = owner_key(require_user(request))
            except APIError as exc:
                return error_response(exc)

    def process_exception(self, request, exception):
        if isinstance(exception, APIError):
            return error_response(exception)
        if isinstance(exception, (JobNotFound, JobConflict, QueueFull)):
            status = (
                404
                if isinstance(exception, JobNotFound)
                else 409
                if isinstance(exception, JobConflict)
                else 429
            )
            return JsonResponse(
                {"detail": str(exception)},
                status=status,
                headers={"Retry-After": "60"} if status == 429 else {},
            )
        if isinstance(exception, RequestDataTooBig):
            return JsonResponse(
                {"detail": "Request fields exceed the size limit."}, status=413
            )
        if isinstance(exception, (SuspiciousOperation, MultiPartParserError)):
            return JsonResponse({"detail": "Invalid multipart request."}, status=400)
