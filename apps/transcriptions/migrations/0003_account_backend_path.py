"""Keep active sessions working after moving authentication into the accounts app."""

from django.core import signing
from django.db import migrations
from django.utils import timezone


def update_sessions(apps, schema_editor):
    Session = apps.get_model("sessions", "Session")
    sessions = Session.objects.using(schema_editor.connection.alias).filter(
        expire_date__gt=timezone.now()
    )
    for session in sessions.iterator():
        try:
            data = signing.loads(
                session.session_data, salt="django.contrib.sessions.SessionStore"
            )
        except (signing.BadSignature, ValueError):
            continue
        if data.get("_auth_user_backend") == "web.backends.RateLimitedModelBackend":
            data["_auth_user_backend"] = (
                "apps.accounts.backends.RateLimitedModelBackend"
            )
            session.session_data = signing.dumps(
                data, salt="django.contrib.sessions.SessionStore", compress=True
            )
            session.save(update_fields=["session_data"])


class Migration(migrations.Migration):
    dependencies = [("web", "0002_django_user_ownership"), ("sessions", "0001_initial")]
    operations = [migrations.RunPython(update_sessions, migrations.RunPython.noop)]
