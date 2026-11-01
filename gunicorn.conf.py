"""One process owns the queue; HTTP threads share its two transcription workers."""

import os

bind = "0.0.0.0:8000"
workers = 1
worker_class = "gthread"
threads = 8
# Legacy /transcribe waits for its result; /jobs returns immediately.
timeout = 0
graceful_timeout = 30
preload_app = False
accesslog = "-"
errorlog = "-"
# Proxy trust is explicit; Django sees Gunicorn's trusted wsgi.url_scheme.
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1,::1")


def worker_exit(server, worker):
    from apps.transcriptions.services.runtime import stop_queue

    stop_queue()
