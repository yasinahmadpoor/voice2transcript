"""Encrypt provider credentials using the application's persistent signing key."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _cipher():
    key = hashlib.sha256(
        ("provider-credentials:" + settings.SECRET_KEY).encode()
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_credential(value):
    return _cipher().encrypt(value.encode()).decode() if value else ""


def decrypt_credential(value):
    try:
        return _cipher().decrypt(value.encode()).decode() if value else ""
    except InvalidToken:
        raise RuntimeError(
            "Provider key cannot be decrypted. Re-enter it in admin."
        ) from None
