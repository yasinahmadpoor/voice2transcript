"""Keep Django signing keys stable across container restarts."""

import os
import secrets
from pathlib import Path


def signing_key(directory):
    configured = os.getenv("DJANGO_SECRET_KEY")
    if configured:
        return configured
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / ".django-secret-key"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    value = secrets.token_urlsafe(64)
    with os.fdopen(descriptor, "w") as destination:
        destination.write(value)
    return value
