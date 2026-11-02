"""Django forms validate the existing JSON and multipart API contracts."""

import re
from urllib.parse import urlsplit

from django import forms

from common.http import APIError

from .defaults import transcription_defaults
from .models import ModelProvider
from .services.prompts import (
    MAX_MEETING_DESCRIPTION,
    MAX_PARTICIPANTS,
    MAX_PARTICIPANT_DESCRIPTION,
    MAX_PARTICIPANT_NAME,
)


class ModelProviderAdminForm(forms.ModelForm):
    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Leave blank to keep the saved key. Gemini requires a saved API key. Local OpenAI-compatible services may need no key.",
    )
    clear_api_key = forms.BooleanField(
        required=False, help_text="Remove the saved key."
    )

    class Meta:
        model = ModelProvider
        exclude = ["api_key_encrypted", "is_default"]

    def clean_api_key(self):
        value = self.cleaned_data["api_key"]
        if "\n" in value or "\r" in value:
            raise forms.ValidationError("API keys cannot contain line breaks.")
        return value

    def clean(self):
        data = super().clean()
        has_key = bool(data.get("api_key")) or (
            bool(self.instance.api_key_encrypted) and not data.get("clear_api_key")
        )
        if data.get("kind") == ModelProvider.Kind.GEMINI and data.get("base_url"):
            previous = (
                ModelProvider.objects.filter(pk=self.instance.pk)
                .values_list("base_url", flat=True)
                .first()
                if self.instance.pk
                else None
            )
            try:
                previous_host = urlsplit(previous or "").hostname
            except ValueError:
                previous_host = None
            changed_host = (
                bool(self.instance.pk)
                and previous_host != urlsplit(data["base_url"]).hostname
            )
            if not has_key or (changed_host and not data.get("api_key")):
                self.add_error(
                    "api_key",
                    "Enter the API key issued by this Gemini provider. Re-enter it when changing hosts.",
                )
        return data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get("api_key"):
            instance.set_api_key(self.cleaned_data["api_key"])
        elif self.cleaned_data.get("clear_api_key"):
            instance.set_api_key("")
        if commit:
            instance.save()
        return instance


class ParticipantListField(forms.JSONField):
    # An empty array is valid optional context, rather than a missing value.
    empty_values = (None, "")


class PromptContextForm(forms.Form):
    prompt = forms.CharField(strip=False)
    meeting_description = forms.CharField(
        required=False, max_length=MAX_MEETING_DESCRIPTION
    )
    participants = ParticipantListField(required=False)

    def clean_participants(self):
        participants = self.cleaned_data["participants"]
        if not isinstance(participants, list):
            raise forms.ValidationError(
                "Participants must be a JSON array of names and descriptions."
            )
        if len(participants) > MAX_PARTICIPANTS:
            raise forms.ValidationError(f"Add at most {MAX_PARTICIPANTS} participants.")
        cleaned = []
        for index, participant in enumerate(participants, start=1):
            if (
                not isinstance(participant, dict)
                or set(participant) - {"name", "description"}
                or not isinstance(participant.get("name", ""), str)
                or not isinstance(participant.get("description", ""), str)
            ):
                raise forms.ValidationError(
                    f"Participant {index} must have a text name and description."
                )
            name = participant.get("name", "").strip()
            description = participant.get("description", "").strip()
            if not name and not description:
                continue
            if not name:
                raise forms.ValidationError(f"Enter a name for participant {index}.")
            if (
                len(name) > MAX_PARTICIPANT_NAME
                or len(description) > MAX_PARTICIPANT_DESCRIPTION
            ):
                raise forms.ValidationError(
                    f"Participant {index}: names allow 100 characters and descriptions allow 1,000 characters."
                )
            cleaned.append({"name": name, "description": description})
        return cleaned


class TranscriptionForm(PromptContextForm):
    file = forms.FileField(allow_empty_file=True)
    provider = forms.CharField()
    model = forms.CharField(strip=False)
    chunk_minutes = forms.FloatField(min_value=0, max_value=120)
    language_hint = forms.CharField(max_length=100, required=False, strip=False)
    fallback_model = forms.CharField(required=False, strip=False)

    def __init__(self, data, files):
        selected = data.get("provider") if "provider" in data else None
        parameters = transcription_defaults(selected)
        for name in parameters:
            if name in data:
                value = data[name]
                if value != "" or name in {"language_hint", "fallback_model"}:
                    parameters[name] = value
        super().__init__(parameters, files)

    def clean_provider(self):
        from .services.providers import resolve_provider

        value = self.cleaned_data["provider"]
        resolve_provider(value)
        return value


def request_key(request):
    key = request.headers.get("Idempotency-Key")
    if key is not None and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) is None:
        raise APIError(
            422,
            "Idempotency-Key must contain 1–128 letters, digits, underscores, or hyphens.",
        )
    return key
