import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
import django

django.setup()

from config.secrets import signing_key


class SettingsEnvironmentTests(unittest.TestCase):
    root = Path(__file__).resolve().parent.parent

    def inspect_settings(self, environment, directory):
        script = """
import json, os
from django.conf import settings
print(json.dumps({
    "root": str(settings.BASE_DIR),
    "data": str(settings.DATA_DIR),
    "database": str(settings.DATABASES["default"]["NAME"]),
    "debug": settings.DEBUG,
    "refresh": settings.WHITENOISE_AUTOREFRESH,
    "secure_session": settings.SESSION_COOKIE_SECURE,
    "secure_csrf": settings.CSRF_COOKIE_SECURE,
    "hosts": settings.ALLOWED_HOSTS,
    "configured_key": settings.SECRET_KEY == os.environ["DJANGO_SECRET_KEY"],
    "templates": str(settings.TEMPLATES[0]["DIRS"][0]),
    "static": str(settings.STATICFILES_DIRS[0]),
}))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=self.root,
            env=os.environ
            | {
                "DJANGO_SETTINGS_MODULE": f"config.settings.{environment}",
                "JOB_DATA_DIR": str(directory),
                "DJANGO_SECRET_KEY": "environment-probe-only-signing-key",
                "SECURE_COOKIES": "true",
                "DJANGO_ALLOWED_HOSTS": "example.test, localhost",
            },
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_development_and_production_keep_configured_paths_and_security(self):
        with tempfile.TemporaryDirectory() as directory:
            for environment in ("development", "production"):
                with self.subTest(environment=environment):
                    settings = self.inspect_settings(environment, directory)
                    self.assertEqual(settings["root"], str(self.root))
                    self.assertEqual(settings["data"], directory)
                    self.assertEqual(
                        settings["database"], str(Path(directory) / "jobs.sqlite3")
                    )
                    self.assertEqual(settings["debug"], environment == "development")
                    self.assertEqual(settings["refresh"], environment == "development")
                    self.assertTrue(settings["configured_key"])
                    self.assertTrue(settings["secure_session"])
                    self.assertTrue(settings["secure_csrf"])
                    self.assertEqual(settings["hosts"], ["example.test", "localhost"])
                    self.assertEqual(settings["templates"], str(self.root / "templates"))
                    self.assertEqual(settings["static"], str(self.root / "static"))

    def test_test_environment_does_not_touch_configured_app_data(self):
        with tempfile.TemporaryDirectory() as directory:
            app_data = Path(directory) / "persisted-data"
            settings = self.inspect_settings("test", app_data)
            self.assertFalse(app_data.exists())
            self.assertNotEqual(settings["data"], str(app_data))
            self.assertFalse(Path(settings["data"]).exists())
            self.assertTrue(settings["database"].startswith(settings["data"]))
            self.assertFalse(settings["debug"])
            self.assertFalse(settings["configured_key"])
            self.assertFalse(settings["secure_session"])
            self.assertFalse(settings["secure_csrf"])

    def test_management_defaults_and_explicit_environment_override(self):
        for command, configured, expected in (
            ("check", None, "development"),
            ("test", None, "test"),
            ("check", "config.settings.production", "production"),
            ("test", "config.settings.production", "production"),
        ):
            with (
                self.subTest(command=command, configured=configured),
                patch.dict(os.environ),
                patch.object(sys, "argv", ["manage.py", command]),
                patch("common.environment.load_env_defaults"),
                patch("django.core.management.execute_from_command_line") as execute,
            ):
                os.environ.pop("DJANGO_SETTINGS_MODULE", None)
                if configured:
                    os.environ["DJANGO_SETTINGS_MODULE"] = configured
                runpy.run_path(str(self.root / "manage.py"), run_name="__main__")
                self.assertEqual(
                    os.environ["DJANGO_SETTINGS_MODULE"], f"config.settings.{expected}"
                )
                execute.assert_called_once_with(["manage.py", command])


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
