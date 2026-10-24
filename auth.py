"""Single-account authentication for the web app (one Uvicorn worker)."""

import hashlib
import hmac
import os
import secrets
import threading
import time
from collections import OrderedDict

from fastapi import HTTPException, Request

COOKIE_NAME = "voice_session"
SESSION_SECONDS = 12 * 60 * 60
_lock = threading.Lock()
_sessions: dict[str, float] = {}
_attempts: OrderedDict[str, list[float]] = OrderedDict()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return f"pbkdf2_sha256$600000${salt}${digest.hex()}"


def configured_username() -> str:
    username = os.getenv("APP_USERNAME", "").strip()
    if not username or not os.getenv("APP_PASSWORD_HASH"):
        raise HTTPException(503, "Login is not configured. Run python setup_login.py on the server first.")
    return username


def verify_credentials(username: str, password: str) -> bool:
    expected_username = configured_username()
    try:
        algorithm, iterations, salt, expected = os.environ["APP_PASSWORD_HASH"].split("$")
        if algorithm != "pbkdf2_sha256" or not 100_000 <= int(iterations) <= 2_000_000:
            raise ValueError("Invalid password hash")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    except (ValueError, KeyError) as exc:
        raise HTTPException(503, "Login configuration is invalid. Run python setup_login.py again.") from exc
    return hmac.compare_digest(username.encode(), expected_username.encode()) & hmac.compare_digest(actual, expected)


def check_login_rate(request: Request) -> None:
    # Bound memory and attempts even when clients rotate usernames.
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _lock:
        attempts = [t for t in _attempts.get(key, []) if now - t < 60]
        if len(attempts) >= 5:
            raise HTTPException(429, "Too many login attempts. Try again in a minute.", headers={"Retry-After": "60"})
        attempts.append(now)
        _attempts[key] = attempts
        _attempts.move_to_end(key)
        while len(_attempts) > 1024:
            _attempts.popitem(last=False)


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    now = time.monotonic()
    with _lock:
        for old_token, expiry in list(_sessions.items()):
            if expiry <= now:
                del _sessions[old_token]
        if len(_sessions) >= 1000:
            del _sessions[next(iter(_sessions))]
        _sessions[token] = now + SESSION_SECONDS
    return token


def require_user(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME, "")
    with _lock:
        expiry = _sessions.get(token, 0)
        if expiry <= time.monotonic():
            _sessions.pop(token, None)
            raise HTTPException(401, "Please sign in to continue.")
    return configured_username()


def delete_session(request: Request) -> None:
    with _lock:
        _sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
