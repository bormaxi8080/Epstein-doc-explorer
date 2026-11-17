#!/usr/bin/env python3
"""Bounded real container acceptance with private synthetic data and scoped cleanup."""

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

from explorer_ingest.distribution_contracts import ExternalDirectory
from explorer_ingest.distribution_demo import demo_bundle
from explorer_ingest.state import atomic_json, digest_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--system-reserve-bytes", type=int, default=15 * 1024**3)
    args = parser.parse_args()
    if args.system_reserve_bytes < 0:
        parser.error("Nonnegative system reserve required")
    storage = ExternalDirectory(args.scratch, reserve_bytes=2 * 1024**3)
    token = uuid.uuid4().hex[:12]
    root = storage.mkdir("containers-" + token)
    images = json.loads(Path("deploy/images.json").read_text())
    builder = "edx-check-" + token
    container = "edx-viewer-" + token
    tags = {name: "edx-distribution-" + name + ":" + token for name in ("viewer", "processor")}
    loaded, created = [], False
    system_before = shutil.disk_usage("/").free
    if system_before < args.system_reserve_bytes + 5 * 1024**3:
        raise ValueError("Container checks reserve 20 GiB on the system volume")
    log = (root / "containers.log").open("w")

    def run(command, capture=False, timeout=900):
        storage.check()
        if shutil.disk_usage("/").free < args.system_reserve_bytes:
            raise ValueError("System disk safety floor reached")
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE if capture else log,
            stderr=log,
            text=True,
            timeout=timeout,
        )
        if result.returncode:
            raise RuntimeError("Container check command failed; inspect external containers.log")
        return result.stdout.strip() if capture else None

    try:
        run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container"])
        created = True
        for name, dockerfile, argument in (
            ("viewer", "deploy/Dockerfile", "NODE_IMAGE"),
            ("processor", "deploy/processor.Dockerfile", "PYTHON_IMAGE"),
        ):
            print("Building " + name, flush=True)
            archive = root / (name + ".tar")
            run(
                [
                    "docker",
                    "buildx",
                    "build",
                    "--builder",
                    builder,
                    "--progress=plain",
                    "--file",
                    dockerfile,
                    "--build-arg",
                    argument + "=" + images["node" if name == "viewer" else "python"],
                    "--tag",
                    tags[name],
                    "--output",
                    "type=docker,dest=" + str(archive),
                    ".",
                ]
            )
            run(["docker", "load", "--input", str(archive)])
            loaded.append(tags[name])
        print("Checking Viewer and bundled UI", flush=True)
        data = root / "data"
        data.mkdir()
        demo_bundle(data / "demo", reserve_bytes=0)
        (data / "current").symlink_to("demo", target_is_directory=True)
        before = digest_file(data / "demo/document_analysis.db")
        run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                container,
                "--memory",
                "512m",
                "--cpus",
                "2",
                "--publish",
                "127.0.0.1::3001",
                "--mount",
                "type=bind,source=" + str(data) + ",target=/data,readonly",
                tags["viewer"],
            ]
        )
        port = run(["docker", "port", container, "3001/tcp"], capture=True).rsplit(":", 1)[1]
        url = "http://127.0.0.1:" + port

        def get(path):
            with urllib.request.urlopen(url + path, timeout=10) as response:
                return response.read()

        for _attempt in range(100):
            try:
                assert json.loads(get("/health"))["status"] == "ok"
                break
            except OSError:
                time.sleep(0.2)
        else:
            raise RuntimeError("Viewer failed to start")
        stats = json.loads(get("/api/stats"))
        catalogue = json.loads(get("/api/documents?limit=10"))
        assert stats["totalDocuments"]["count"] == len(catalogue["documents"]) == 1
        assert "SYNTHETIC" in catalogue["documents"][0]["one_sentence_summary"]
        html = get("/")
        assert b"<html" in html.lower() and b"/assets/" in html
        assert before == digest_file(data / "demo/document_analysis.db")
        print("Checking offline processor entry points", flush=True)
        workspace = root / "workspace"
        workspace.mkdir()
        for name in ("packages", "inventory", "originals", "corpus", "releases", "tmp"):
            (workspace / name).mkdir()
        config = dict(
            format="explorer-workspace",
            version=1,
            root="/workspace",
            reserve_bytes=0,
            paths=dict(
                packages="packages",
                inventory="inventory",
                downloads="originals",
                corpus="corpus",
                releases="releases",
                scratch="tmp",
            ),
        )
        atomic_json(workspace / "workspace.json", config)
        options = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "512m",
            "--cpus",
            "2",
            "--user",
            str(os.getuid()) + ":" + str(os.getgid()),
            "--mount",
            "type=bind,source=" + str(workspace) + ",target=/workspace",
        ]
        run(
            options
            + [
                "--entrypoint",
                "explorer-data",
                tags["processor"],
                "doctor",
                "--component",
                "processor",
                "--workspace",
                "/workspace/workspace.json",
            ]
        )
        run(options + [tags["processor"], "--help"])
        run(
            options
            + [
                "--entrypoint",
                "explorer-data",
                tags["processor"],
                "demo",
                "--output",
                "/workspace/releases/demo",
                "--reserve-bytes",
                "0",
            ]
        )
        result = dict(
            passed=True,
            viewer_documents=1,
            ui_served=True,
            database_unchanged=True,
            processor_offline=True,
            host_architecture=run(
                ["docker", "version", "--format", "{{.Server.Arch}}"], capture=True
            ),
            image_inputs=images,
            system_free_before=system_before,
            system_free_after=shutil.disk_usage("/").free,
            output=str(root),
        )
        atomic_json(root / "verification.json", result)
        print(json.dumps(result, indent=2), flush=True)
    finally:
        subprocess.run(["docker", "rm", "--force", container], stdout=log, stderr=log, timeout=30)
        for image in loaded:
            subprocess.run(["docker", "image", "rm", image], stdout=log, stderr=log, timeout=60)
        if created:
            subprocess.run(
                ["docker", "buildx", "rm", "--force", builder], stdout=log, stderr=log, timeout=60
            )
        log.close()


if __name__ == "__main__":
    main()
