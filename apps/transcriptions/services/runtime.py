"""Own the queue's process lifetime without starting it during management commands."""

import atexit
import os
import threading
from pathlib import Path

from django.conf import settings

from apps.transcriptions.services.queue import JobQueue

from . import jobs
from .pipeline import require_binary

_lock = threading.Lock()
_queue = None


def get_queue():
    global _queue
    with _lock:
        if _queue is None:
            require_binary("ffmpeg")
            require_binary("ffprobe")
            queue = JobQueue(
                Path(settings.DATA_DIR).resolve(),
                lambda *args, **kwargs: jobs.run_pipeline(*args, **kwargs),
                workers=int(os.getenv("TRANSCRIPTION_WORKERS", "2")),
                max_pending=int(os.getenv("MAX_PENDING_JOBS", "20")),
            )
            queue.start()
            _queue = queue
        return _queue


def stop_queue():
    global _queue
    with _lock:
        if _queue is not None:
            _queue.stop()
            if _queue._lock_file is None:
                _queue = None


atexit.register(stop_queue)
