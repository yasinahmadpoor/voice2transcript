"""Isolate provider calls so a worker can stop one job without stopping others."""

import json
import os
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar

_cancellation = ContextVar("transcription_cancellation", default=None)


class JobCancelled(Exception):
    pass


@contextmanager
def cancellation_scope(check):
    token = _cancellation.set(check)
    try:
        yield
    finally:
        _cancellation.reset(token)


def check_cancelled():
    check = _cancellation.get()
    if check is not None and check():
        raise JobCancelled()


def _terminate(process):
    if process.poll() is not None:
        return
    # ffmpeg/ffprobe inherit the session: stop those children as well.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_isolated(parameters):
    from django.conf import settings

    check_cancelled()
    # The parent owns this directory, including the child's converted chunks.
    # It can clean everything even when cancellation kills the child abruptly.
    with tempfile.TemporaryDirectory(prefix="transcription_worker_") as directory:
        environment = os.environ | {
            "DJANGO_SETTINGS_MODULE": settings.SETTINGS_MODULE,
            "JOB_DATA_DIR": str(settings.DATA_DIR),
            "TMPDIR": directory,
        }
        with tempfile.TemporaryFile(dir=directory) as output:
            process = subprocess.Popen(
                [sys.executable, "-m", "apps.transcriptions.services.worker"],
                cwd=settings.BASE_DIR,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                payload = json.dumps(parameters).encode()
                while True:
                    check_cancelled()
                    try:
                        process.communicate(input=payload, timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        payload = None
                check_cancelled()
                if process.returncode == 2:
                    from .checkpoints import CheckpointMismatch

                    output.seek(0)
                    if json.load(output) == {"error": "checkpoint_mismatch"}:
                        raise CheckpointMismatch()
                if process.returncode:
                    raise RuntimeError(
                        "Transcription worker failed. Check provider configuration."
                    )
                output.seek(0)
                result = json.load(output)
                if not isinstance(result, str):
                    raise RuntimeError(
                        "Transcription worker returned an invalid result."
                    )
                return result
            finally:
                _terminate(process)
                process.stdin.close()
