"""Private subprocess entry point; credentials are resolved from Django's DB."""

import json
import signal
import sys
from contextlib import redirect_stdout
from pathlib import Path


def main():
    # Raise out of blocking provider calls on termination, allowing normal
    # cleanup where possible. The parent enforces a one-second shutdown limit.
    from .execution import JobCancelled

    def terminate(signum, frame):
        raise JobCancelled()

    signal.signal(signal.SIGTERM, terminate)
    try:
        import django

        django.setup()
        from .jobs import execute_pipeline

        parameters = json.load(sys.stdin)
        parameters["input_path"] = Path(parameters["input_path"])
        with redirect_stdout(sys.stderr):
            result = execute_pipeline(**parameters)
        json.dump(result, sys.stdout)
    except Exception:
        # Provider errors may include secrets; the parent reports a safe error.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
