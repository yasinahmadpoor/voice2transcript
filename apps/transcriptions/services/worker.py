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
    from .checkpoints import CheckpointMismatch

    def terminate(signum, frame):
        raise JobCancelled()

    signal.signal(signal.SIGTERM, terminate)
    try:
        import django

        django.setup()
        from .jobs import execute_pipeline

        parameters = json.load(sys.stdin)
        with redirect_stdout(sys.stderr):
            if "document_export" in parameters:
                import base64
                from .exports import render_export

                result = base64.b64encode(
                    render_export(**parameters["document_export"])
                ).decode("ascii")
            elif "connection_test_provider" in parameters:
                from .connection_test import execute_connection_test

                result = json.dumps(
                    execute_connection_test(parameters["connection_test_provider"])
                )
            else:
                parameters["input_path"] = Path(parameters["input_path"])
                result = execute_pipeline(**parameters)
        json.dump(result, sys.stdout)
    except CheckpointMismatch:
        json.dump({"error": "checkpoint_mismatch"}, sys.stdout)
        return 2
    except Exception:
        # Provider errors may include secrets; the parent reports a safe error.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
