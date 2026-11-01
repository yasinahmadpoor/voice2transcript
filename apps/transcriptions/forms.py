"""Django forms validate the existing JSON and multipart API contracts."""

import re

from django import forms

from common.http import APIError

from .defaults import transcription_defaults


class TranscriptionForm(forms.Form):
    file = forms.FileField(allow_empty_file=True)
    model = forms.CharField(strip=False)
    chunk_minutes = forms.FloatField(min_value=0, max_value=120)
    language_hint = forms.CharField(max_length=100, required=False, strip=False)
    fallback_model = forms.CharField(required=False, strip=False)
    prompt = forms.CharField(strip=False)

    def __init__(self, data, files):
        parameters = transcription_defaults()
        for name in parameters:
            if name in data:
                value = data[name]
                if value != "" or name in {"language_hint", "fallback_model"}:
                    parameters[name] = value
        super().__init__(parameters, files)


def request_key(request):
    key = request.headers.get("Idempotency-Key")
    if key is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) is None:
        raise APIError(
            422,
            "Idempotency-Key must contain 1–128 letters, digits, underscores, or hyphens.",
        )
    return key
