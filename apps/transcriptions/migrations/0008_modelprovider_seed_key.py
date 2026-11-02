from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("web", "0007_job_checkpoints")]

    operations = [
        migrations.AddField(
            model_name="modelprovider",
            name="seed_key",
            field=models.CharField(
                blank=True, editable=False, max_length=50, null=True, unique=True
            ),
        ),
    ]
