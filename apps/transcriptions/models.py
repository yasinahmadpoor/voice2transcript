"""Django's read-only ORM mapping of the existing persistent queue table."""

from django.db import models


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
