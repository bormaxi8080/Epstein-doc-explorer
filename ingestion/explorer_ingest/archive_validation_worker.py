"""Read one controlled validation request; never execute document instructions."""

import json
import sys

from .archive_limits import worker_limits


def main():
    request = json.loads(sys.stdin.read())
    settings = request["options"]
    worker_limits(
        settings["validation_timeout"], settings["validation_memory_mb"], request["parent_pid"]
    )
    from pathlib import Path

    from .archive_transfer import IntegrityError, Options, _validate_content

    try:
        result = _validate_content(Path(request["path"]), request["metadata"], Options(**settings))
        response = {"result": result}
    except Exception as exc:
        response = {
            "error_type": type(exc).__name__,
            "message": str(exc) if isinstance(exc, IntegrityError) else type(exc).__name__,
        }
    print(json.dumps(response))


if __name__ == "__main__":
    main()
