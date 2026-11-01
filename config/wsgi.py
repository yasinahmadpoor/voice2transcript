"""Start one persistent queue per serving process, before accepting requests."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()

from apps.transcriptions.services.runtime import get_queue

get_queue()
