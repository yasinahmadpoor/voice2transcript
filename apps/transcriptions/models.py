"""Django's read-only ORM mapping of the existing persistent queue table."""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import models, transaction

from common.credentials import decrypt_credential, encrypt_credential


class ModelProvider(models.Model):
    class Kind(models.TextChoices):
        GEMINI = "gemini", "Gemini"
        OPENAI = "openai", "OpenAI-compatible audio transcription"

    name = models.CharField(max_length=100, unique=True)
    seed_key = models.CharField(
        max_length=50, unique=True, null=True, blank=True, editable=False
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    base_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Gemini: leave blank for Google, or use a proxy root such as https://api.metisai.ir. OpenAI-compatible: enter its API root, including /v1 if needed.",
    )
    api_key_encrypted = models.TextField(blank=True, editable=False)
    model = models.CharField(
        max_length=200, help_text="Audio-capable model identifier."
    )
    fallback_model = models.CharField(max_length=200, blank=True)
    is_enabled = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["is_default"],
                condition=models.Q(is_default=True),
                name="one_default_model_provider",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        if self.is_default and not self.is_enabled:
            raise ValidationError(
                {
                    "is_enabled": "Choose another default provider before disabling this one."
                }
            )
        if self.base_url or self.kind == self.Kind.OPENAI:
            try:
                url = urlsplit(self.base_url)
            except ValueError:
                raise ValidationError(
                    {"base_url": "Enter a valid HTTP(S) API root."}
                ) from None
            if (
                not url.hostname
                or url.scheme not in {"http", "https"}
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValidationError(
                    {
                        "base_url": "Enter an HTTP(S) API root without credentials, query parameters, or a fragment."
                    }
                )

    def set_api_key(self, value):
        if "\n" in value or "\r" in value:
            raise ValidationError("API keys cannot contain line breaks.")
        self.api_key_encrypted = encrypt_credential(value)

    def get_api_key(self):
        return decrypt_credential(self.api_key_encrypted)

    def make_default(self):
        with transaction.atomic():
            if not type(self).objects.filter(pk=self.pk, is_enabled=True).exists():
                raise ValidationError("Choose an enabled provider.")
            type(self).objects.filter(is_default=True).update(is_default=False)
            type(self).objects.filter(pk=self.pk).update(is_default=True)
        self.is_default = True


class JobCheckpoint(models.Model):
    job_id = models.CharField(max_length=32, primary_key=True)
    fingerprint = models.CharField(max_length=64)
    plan = models.JSONField()
    completed = models.JSONField(default=dict)

    class Meta:
        db_table = "job_checkpoints"


class TranscriptionJob(models.Model):
    sequence = models.AutoField(primary_key=True)
    id = models.CharField(max_length=32, unique=True)
    owner = models.CharField(max_length=128)
    filename = models.TextField()
    input_path = models.TextField()
    settings = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=[
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("cancelling", "Cancelling"),
            ("cancelled", "Cancelled"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
    )
    created_at = models.FloatField()
    queued_at = models.FloatField()
    started_at = models.FloatField(null=True)
    finished_at = models.FloatField(null=True)
    transcript = models.TextField(null=True)
    error = models.TextField(null=True)
    interrupted = models.BooleanField(default=False)
    request_key = models.CharField(max_length=128, null=True)

    class Meta:
        managed = False
        db_table = "jobs"
        ordering = ["-sequence"]
        verbose_name = "transcription job"

    def __str__(self):
        return self.filename
