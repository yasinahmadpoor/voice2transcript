"""Import the initial Gemini configuration without storing plaintext fixtures."""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from ...services.seeding import seed_gemini_provider


class Command(BaseCommand):
    help = "Seed Gemini once, encrypting GEMINI_API_KEY or GOOGLE_API_KEY from the environment."

    def handle(self, *args, **options):
        try:
            provider, created = seed_gemini_provider()
        except ValidationError:
            raise CommandError(
                "Cannot seed Gemini: check the initial provider configuration and API key format."
            ) from None
        if not created:
            self.stdout.write(
                "Gemini seed already exists; database settings were preserved."
            )
        elif not provider.api_key_encrypted:
            self.stdout.write(
                self.style.WARNING(
                    "Gemini created without an API key and disabled. Add its key and enable it in admin."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Gemini provider created with an encrypted API key.")
            )
