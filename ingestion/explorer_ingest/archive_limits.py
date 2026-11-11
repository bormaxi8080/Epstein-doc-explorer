"""Bound parser workers by wall time, resident memory and child lifetime."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


class ValidationBudgetError(ValueError):
    """Validation could not finish within the explicit resource budget."""


def resident_bytes(pid):
    if sys.platform.startswith("linux"):
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except FileNotFoundError:
            return 0
    elif sys.platform == "darwin":
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1,
            check=False,
        )
        return int(result.stdout.strip() or 0) * 1024
    raise ValidationBudgetError("Resident-memory monitoring is unavailable")


def run_limited(command, payload, options, check=lambda: None):
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        start_new_session=True,
    )
    started, request = time.monotonic(), json.dumps(payload)
    monitor_failures = 0
    try:
        while True:
            check()
            if time.monotonic() - started > options.validation_timeout:
                raise ValidationBudgetError("Content validation wall-time budget exceeded")
            try:
                resident = resident_bytes(process.pid)
                monitor_failures = 0
            except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
                monitor_failures += 1
                if monitor_failures >= 3:
                    raise ValidationBudgetError(
                        "Resident-memory monitoring failed after three attempts"
                    ) from exc
                time.sleep(0.05)
                continue
            if resident > options.validation_memory_mb * 1024**2:
                raise ValidationBudgetError("Content validation resident-memory budget exceeded")
            try:
                output, _ = process.communicate(input=request, timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                request = None
        if process.returncode:
            raise ValidationBudgetError("Content validator exited before publishing a result")
        if len(output) > 65536:
            raise ValidationBudgetError("Content validator output budget exceeded")
        return json.loads(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def worker_limits(seconds, memory_mb, parent_pid):
    import math
    import resource
    import signal
    import threading

    signal.alarm(max(1, math.ceil(seconds)))
    resource.setrlimit(
        resource.RLIMIT_CPU, (max(1, math.ceil(seconds)), max(2, math.ceil(seconds) + 1))
    )
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024**2, memory_mb * 1024**2))

    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(0.1)
        os._exit(72)

    threading.Thread(target=watch_parent, daemon=True).start()
