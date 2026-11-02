"""Initial provider configuration. Existing database settings always win."""

import os

from django.db import transaction

from ..models import ModelProvider
from .pipeline import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL

GEMINI_SEED_KEY = "default-gemini"


def seed_gemini_provider():
    with transaction.atomic():
        existing = ModelProvider.objects.filter(seed_key=GEMINI_SEED_KEY).first()
        if existing is not None:
            return existing, False
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or ""
        name = "Gemini"
        suffix = 1
        while ModelProvider.objects.filter(name=name).exists():
            suffix += 1
            name = f"Gemini (seeded {suffix})"
        provider = ModelProvider(
            seed_key=GEMINI_SEED_KEY,
            name=name,
            kind=ModelProvider.Kind.GEMINI,
            base_url="",
            model=DEFAULT_MODEL,
            fallback_model=DEFAULT_FALLBACK_MODEL,
            is_enabled=bool(key),
            is_default=bool(key)
            and not ModelProvider.objects.filter(is_default=True).exists(),
        )
        provider.set_api_key(key)
        provider.full_clean()
        provider.save()
        return provider, True
