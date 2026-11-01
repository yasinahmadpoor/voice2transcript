"""Import the original administrator and attach legacy jobs to stable user IDs."""

import base64
import os
from uuid import uuid4

from django.db import migrations


def migrate_owners(apps, schema_editor):
    User = apps.get_model("auth", "User")
    alias = schema_editor.connection.alias
    username = os.getenv("APP_USERNAME", "").strip()
    encoded = os.getenv("APP_PASSWORD_HASH", "")
    if username and encoded:
        try:
            algorithm, iterations, salt, digest = encoded.split("$")
            if (
                algorithm != "pbkdf2_sha256"
                or not 100_000 <= int(iterations) <= 2_000_000
                or len(bytes.fromhex(digest)) != 32
            ):
                raise ValueError("Invalid legacy hash")
            password = f"{algorithm}${iterations}${salt}${base64.b64encode(bytes.fromhex(digest)).decode()}"
        except ValueError as exc:
            raise RuntimeError(
                "Invalid bootstrap login configuration; check APP_PASSWORD_HASH."
            ) from exc
        # Existing Django passwords, roles, and inactive flags are authoritative.
        User.objects.using(alias).get_or_create(
            username=username,
            defaults={
                "password": password,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
    if "jobs" not in schema_editor.connection.introspection.table_names():
        return
    accounts = dict(User.objects.using(alias).values_list("username", "pk"))
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT sequence, owner FROM jobs")
        rows = cursor.fetchall()
        prefix = uuid4().hex
        # Avoid collisions with an old username that resembles a new owner ID.
        for sequence, _owner in rows:
            cursor.execute(
                "UPDATE jobs SET owner=%s WHERE sequence=%s",
                [f"migrating:{prefix}:{sequence}", sequence],
            )
        for sequence, owner in rows:
            # Keep unknown legacy accounts inaccessible rather than assigning
            # their recordings to a future user who reuses the username.
            target = (
                f"user:{accounts[owner]}" if owner in accounts else f"legacy:{owner}"
            )
            cursor.execute(
                "UPDATE jobs SET owner=%s WHERE sequence=%s", [target, sequence]
            )


class Migration(migrations.Migration):
    dependencies = [
        ("web", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]
    operations = [migrations.RunPython(migrate_owners)]
