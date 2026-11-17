"""Read-only installation diagnostics; no downloads, inference or directory creation."""

import platform
import re
import shutil
import subprocess
import sys

from . import __version__
from .distribution_contracts import load_workspace


def diagnose(workspace=None, require_docker=False, component="all"):
    if component not in ("all", "viewer", "processor"):
        raise ValueError("Invalid installation component")
    checks = []

    def add(name, ok, detail):
        checks.append(dict(name=name, passed=bool(ok), detail=detail))

    add(
        "python",
        sys.version_info >= (3, 10),
        platform.python_version() + "; 3.11+ supports streamed large TEXT",
    )
    add(
        "platform",
        sys.platform in ("linux", "darwin"),
        sys.platform + "; Windows uses Linux containers",
    )
    for command in ("node", "npm") if component != "processor" else ():
        binary = shutil.which(command)
        if not binary:
            add(command, False, "not installed")
            continue
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
        version = result.stdout.strip()[:100]
        major = re.match(r"v?(\d+)\.(\d+)\.(\d+)", version)
        add(
            command,
            result.returncode == 0
            and (command != "node" or (major and tuple(map(int, major.groups())) >= (22, 12, 0))),
            version,
        )
    if workspace:
        try:
            config, storage = load_workspace(workspace)
            free = shutil.disk_usage(storage.root).free
            add(
                "workspace",
                True,
                dict(
                    root=str(storage.root),
                    free_bytes=free,
                    reserve_bytes=storage.reserve_bytes,
                    initialized=all(storage.path(p).is_dir() for p in config["paths"].values()),
                ),
            )
        except (OSError, ValueError, KeyError) as exc:
            add("workspace", False, str(exc))
    if require_docker:
        binary = shutil.which("docker")
        if binary:
            try:
                result = subprocess.run(
                    [binary, "version", "--format", "{{.Server.Version}}"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                add(
                    "docker",
                    result.returncode == 0,
                    result.stdout.strip()[:100] or "daemon unavailable",
                )
            except subprocess.TimeoutExpired:
                add("docker", False, "daemon timeout")
        else:
            add("docker", False, "not installed")
    return dict(
        application_version=__version__,
        checks=checks,
        passed=all(c["passed"] for c in checks),
        network_or_model_calls=False,
    )
