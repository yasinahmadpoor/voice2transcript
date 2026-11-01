"""Local development with debug pages and refreshed static assets."""

from config.secrets import signing_key

from .base import *  # noqa: F403

DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)  # noqa: F405
SECRET_KEY = signing_key(DATA_DIR)  # noqa: F405
DEBUG = True
WHITENOISE_AUTOREFRESH = True
