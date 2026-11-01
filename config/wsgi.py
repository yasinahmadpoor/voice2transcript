"""Start one persistent queue per serving process, before accepting requests."""

import os

from django.core.wsgi import get_wsgi_application

from common.environment import load_env_defaults

load_env_defaults()
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
application = get_wsgi_application()

from apps.transcriptions.services.runtime import get_queue

get_queue()
