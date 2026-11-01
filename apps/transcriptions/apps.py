from django.apps import AppConfig


class TranscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.transcriptions"
    # Keep the established migration, permission, and admin URL identity.
    label = "web"
    verbose_name = "Transcriptions"
