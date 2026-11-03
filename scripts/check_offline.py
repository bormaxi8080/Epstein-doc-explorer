#!/usr/bin/env python3
"""Run every synthetic ingestion test with local HTTP fixtures and external sockets blocked."""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = """import ipaddress
import sys

def _loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False

def _offline(event, args):
    if event == 'socket.getaddrinfo' and not _loopback(args[0]):
        raise RuntimeError('Offline tests forbid external DNS')
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple) and not _loopback(address[0]):
            raise RuntimeError('Offline tests forbid external connections')
sys.addaudithook(_offline)
"""


def port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default="test*.py")
    args = parser.parse_args()
    node = shutil.which("node")
    if not node:
        parser.error("Node is required for the existing synthetic HTTP fixture handlers")
    children = []
    logs = []
    try:
        with tempfile.TemporaryDirectory(prefix="explorer-offline-") as directory:
            temporary = Path(directory)
            (temporary / "sitecustomize.py").write_text(GUARD)
            environment = dict(os.environ)
            environment["PYTHONPATH"] = os.pathsep.join((str(temporary), str(ROOT / "ingestion")))
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            for name, variable in (
                ("archive", "ARCHIVE_TEST_BASE_URL"),
                ("fcc", "FCC_TEST_BASE_URL"),
            ):
                listening = port()
                log = (temporary / (name + ".log")).open("w+")
                logs.append(log)
                process = subprocess.Popen(
                    [
                        node,
                        "--input-type=commonjs",
                        "-e",
                        "eval(require('fs').readFileSync(process.argv[1], 'utf8'))",
                        str(ROOT / "postman" / "mocks" / name / "default.js"),
                    ],
                    cwd=ROOT,
                    env={**environment, "PORT": str(listening), "HOST": "127.0.0.1"},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                children.append(process)
                deadline = time.monotonic() + 10
                while True:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("Local fixture failed: " + log.read()[-1000:])
                    try:
                        with socket.create_connection(("127.0.0.1", listening), timeout=0.1):
                            break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Local fixture did not listen in time") from None
                        time.sleep(0.05)
                environment[variable] = f"http://127.0.0.1:{listening}"
            probe = """import socket
for action in (
    lambda: socket.getaddrinfo('offline-probe.invalid', 443),
    lambda: socket.socket().connect(('198.51.100.1', 443)),
):
    try:
        action()
    except RuntimeError:
        pass
    else:
        raise SystemExit('Offline guard is inactive')
"""
            subprocess.run([sys.executable, "-c", probe], env=environment, check=True, timeout=5)
            print(
                "Synthetic archive/FCC fixtures ready on loopback; external Python sockets blocked.",
                flush=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "ingestion/tests",
                    "-p",
                    args.pattern,
                    "-v",
                ],
                cwd=ROOT,
                env=environment,
            )
            return result.returncode
    except (OSError, RuntimeError) as exc:
        print(f"Offline verification failed: {exc}", file=sys.stderr)
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
