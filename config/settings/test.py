"""Isolated test data and signing key, without touching persisted app data."""

import tempfile

from .base import *  # noqa: F403

_test_directory = tempfile.TemporaryDirectory(prefix="voice2transcript-tests-")
DATA_DIR = Path(_test_directory.name)  # noqa: F405
SECRET_KEY = "test-only-signing-key-not-for-development-or-production"
DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "[::1]"]
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "jobs.sqlite3",
        "OPTIONS": {"timeout": 10},
        "TEST": {"NAME": ":memory:"},
    }
}
