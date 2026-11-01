import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from config.secrets import signing_key


class DjangoAuthTests(unittest.TestCase):
    def test_signing_key_is_private_persistent_and_can_be_configured(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"DJANGO_SECRET_KEY": ""}),
        ):
            key = signing_key(directory)
            self.assertGreaterEqual(len(key), 64)
            self.assertEqual(signing_key(directory), key)
            self.assertEqual(
                (Path(directory) / ".django-secret-key").stat().st_mode & 0o777, 0o600
            )
            with patch.dict(
                os.environ, {"DJANGO_SECRET_KEY": "test-explicit-signing-key"}
            ):
                self.assertEqual(signing_key(directory), "test-explicit-signing-key")
