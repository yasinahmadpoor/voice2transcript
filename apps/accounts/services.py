"""Login throttling and legacy password setup helpers."""

import hashlib
import secrets
import threading
import time
from collections import OrderedDict

from common.http import APIError

COOKIE_NAME = "voice_session"
SESSION_SECONDS = 12 * 60 * 60
_lock = threading.Lock()
_attempts: OrderedDict[str, list[float]] = OrderedDict()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return f"pbkdf2_sha256$600000${salt}${digest.hex()}"


def check_login_rate(request) -> None:
    # Bound memory and attempts even when clients rotate usernames.
    key = request.META.get("REMOTE_ADDR", "unknown")
    now = time.monotonic()
    with _lock:
        attempts = [t for t in _attempts.get(key, []) if now - t < 60]
        if len(attempts) >= 5:
            raise APIError(
                429,
                "Too many login attempts. Try again in a minute.",
                headers={"Retry-After": "60"},
            )
        attempts.append(now)
        _attempts[key] = attempts
        _attempts.move_to_end(key)
        while len(_attempts) > 1024:
            _attempts.popitem(last=False)


def owner_key(user) -> str:
    """User IDs survive renames and are never reassigned to newly created users."""
    return f"user:{user.pk}"


def require_user(request):
    user = request.user
    if not user.is_authenticated or not user.is_active:
        raise APIError(401, "Please sign in to continue.")
    return user
