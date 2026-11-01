"""Frontend authentication with Django's built-in user and session models."""

import json
import os

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.http import HttpResponse, JsonResponse

from common.http import APIError, methods, validated

from .forms import LoginForm


@methods("POST")
def login(request):
    if request.content_type != "application/json":
        raise APIError(422, "Send username and password as JSON.")
    try:
        credentials = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(422, "Invalid JSON.") from exc
    if not isinstance(credentials, dict) or any(
        not isinstance(credentials.get(key), str) for key in ("username", "password")
    ):
        raise APIError(422, "Username and password must be strings.")
    credentials = validated(LoginForm(credentials))
    user = authenticate(request, **credentials)
    if user is None:
        raise APIError(401, "Incorrect username or password.")
    django_login(request, user)
    return JsonResponse({"username": user.get_username()})


@methods("GET")
def current_user(request):
    return JsonResponse(
        {
            "username": request.user.get_username(),
            "user_id": request.user.pk,
            "max_upload_mb": int(os.getenv("MAX_UPLOAD_MB", "500")),
        }
    )


@methods("POST")
def logout(request):
    django_logout(request)
    return HttpResponse(status=204)
