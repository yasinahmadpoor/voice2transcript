"""HTTP errors shared by authentication, validation, and views."""

from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt


class APIError(Exception):
    def __init__(self, status_code, detail, headers=None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.headers = headers or {}


def error_response(error):
    return JsonResponse(
        {"detail": error.detail}, status=error.status_code, headers=error.headers
    )


def not_found(request, exception=None):
    return JsonResponse({"detail": "Not Found"}, status=404)


def bad_request(request, exception=None):
    return JsonResponse({"detail": "Invalid request."}, status=400)


def server_error(request):
    return JsonResponse({"detail": "Internal server error."}, status=500)


def methods(*allowed):
    """Keep JSON errors and support HEAD consistently with GET."""

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            accepted = (*allowed, "HEAD") if "GET" in allowed else allowed
            if request.method not in accepted:
                raise APIError(
                    405, "Method Not Allowed", {"Allow": ", ".join(accepted)}
                )
            return view(request, *args, **kwargs)

        # Existing clients use the strict session cookie and origin checks.
        # Django's admin retains its standard CSRF protection.
        return csrf_exempt(wrapped)

    return decorate


def validated(form):
    if not form.is_valid():
        errors = [
            {"loc": ["body", name], "msg": str(message)}
            for name, messages in form.errors.items()
            for message in messages
        ]
        raise APIError(422, errors)
    return form.cleaned_data
