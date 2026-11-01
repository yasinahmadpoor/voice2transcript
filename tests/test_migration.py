"""Verify upgrading a populated single-account database without provider calls."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from apps.accounts import services as auth
from apps.transcriptions.services.queue import JobQueue


class OwnershipMigrationTests(unittest.TestCase):
    def test_legacy_history_and_passwords_survive_account_migration(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ | {
                "JOB_DATA_DIR": directory,
                "DJANGO_SECRET_KEY": "migration-test-only-stable-signing-key",
                "APP_USERNAME": "original-admin",
                "APP_PASSWORD_HASH": auth.hash_password(
                    "original-administrator-password"
                ),
            }
            # Represent a server with native auth enabled, but before user ownership.
            subprocess.run(
                [sys.executable, "manage.py", "migrate", "auth", "--noinput"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                [sys.executable, "manage.py", "migrate", "sessions", "--noinput"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            script = (
                "import os; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');"
                "import django; django.setup();"
                "from django.contrib.auth import get_user_model;"
                "user=get_user_model().objects.create_user(username='alice',password='existing-alice-password');"
                "from django.contrib.sessions.backends.db import SessionStore;"
                "session=SessionStore();"
                "session['_auth_user_id']=str(user.pk);"
                "session['_auth_user_hash']=user.get_session_auth_hash();"
                "session['_auth_user_backend']='web.backends.RateLimitedModelBackend';"
                "session.create()"
            )
            subprocess.run(
                [sys.executable, "-c", script], cwd=root, env=env, check=True
            )
            queue = JobQueue(Path(directory), lambda *args: "unused")
            settings = {
                "model": "test",
                "fallback_model": "",
                "chunk_minutes": 0,
                "language_hint": "",
                "prompt": "Test",
            }
            for owner in ("original-admin", "alice", "future-user", "user:1"):
                job_id = uuid4().hex
                path = queue.uploads / f"{job_id}.media"
                path.write_bytes(b"synthetic")
                queue.enqueue(
                    job_id, owner, f"{owner}.wav", path, settings, "shared-retry-key"
                )
            with sqlite3.connect(queue.database) as db:
                db.execute(
                    "UPDATE jobs SET status='completed', transcript='Saved transcript'"
                )
                before = db.execute(
                    "SELECT id,filename,settings,status,transcript,request_key FROM jobs ORDER BY sequence"
                ).fetchall()
                alice_before = db.execute(
                    "SELECT id,password FROM auth_user WHERE username='alice'"
                ).fetchone()
                session_before = db.execute(
                    "SELECT session_key,session_data,expire_date FROM django_session"
                ).fetchone()
            subprocess.run(
                [sys.executable, "manage.py", "migrate", "--noinput"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            with sqlite3.connect(queue.database) as db:
                after = db.execute(
                    "SELECT id,filename,settings,status,transcript,request_key FROM jobs ORDER BY sequence"
                ).fetchall()
                admin = db.execute(
                    "SELECT id,is_staff,is_superuser,password FROM auth_user WHERE username='original-admin'"
                ).fetchone()
                owners = [
                    row[0]
                    for row in db.execute("SELECT owner FROM jobs ORDER BY sequence")
                ]
                self.assertEqual(
                    db.execute(
                        "SELECT id,password FROM auth_user WHERE username='alice'"
                    ).fetchone(),
                    alice_before,
                )
                session_after = db.execute(
                    "SELECT session_key,session_data,expire_date FROM django_session"
                ).fetchone()
            self.assertEqual(after, before)
            self.assertEqual(admin[1:3], (1, 1))
            self.assertEqual(
                owners,
                [
                    f"user:{admin[0]}",
                    f"user:{alice_before[0]}",
                    "legacy:future-user",
                    "legacy:user:1",
                ],
            )
            # The imported legacy password uses Django's native hash encoding.
            from django.contrib.auth.hashers import check_password

            self.assertTrue(check_password("original-administrator-password", admin[3]))
            self.assertEqual(len(queue.list(f"user:{admin[0]}")["jobs"]), 1)
            self.assertEqual(queue.list("future-user")["jobs"], [])
            from django.core import signing

            decode = lambda data: signing.loads(
                data,
                key=env["DJANGO_SECRET_KEY"],
                salt="django.contrib.sessions.SessionStore",
            )
            old_data, new_data = decode(session_before[1]), decode(session_after[1])
            self.assertEqual(session_before[0], session_after[0])
            self.assertEqual(session_before[2], session_after[2])
            self.assertEqual(
                new_data,
                old_data
                | {
                    "_auth_user_backend": "apps.accounts.backends.RateLimitedModelBackend"
                },
            )
