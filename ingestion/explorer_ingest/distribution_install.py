"""Offline relocation of verified data; machine-bound job ledgers are never copied."""

import json
import shutil
import uuid
from pathlib import Path

from .distribution_contracts import ExternalDirectory, load_release, safe_path
from .distribution_io import decoded, durable, transfer
from .distribution_release import select_artifacts, verify_installed
from .inventory_parts import fsync_directory
from .state import atomic_json, digest_file


def import_release(
    package, output, groups=None, allow_drafts=False, reserve_bytes=1024**3, expected_sha256=None
):
    package = Path(package).absolute()
    source = ExternalDirectory(package, reserve_bytes=0)
    release = load_release(source.path("release.json"), expected_sha256)
    selected = select_artifacts(release, groups, allow_drafts)
    output = Path(output).absolute()
    storage = ExternalDirectory(output.parent, reserve_bytes)
    if output.exists() or output.is_symlink():
        raise ValueError("Install needs a new directory; existing releases are immutable")
    storage.check(sum(a["content_bytes"] for a in selected))
    stage = storage.mkdir(".installing-" + uuid.uuid4().hex)
    try:
        for item in selected:
            incoming_path = source.path(item["path"])
            if (
                incoming_path.stat().st_size != item["bytes"]
                or digest_file(incoming_path) != item["sha256"]
            ):
                raise ValueError("Package artifact checksum mismatch")
            target = safe_path(stage, item["install_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with (
                decoded(incoming_path, item["encoding"]) as incoming,
                target.open("xb") as outgoing,
            ):
                transfer(incoming, outgoing, storage, item["content_bytes"], item["content_sha256"])
                durable(outgoing)
        result = verify_installed(stage, release, selected)
        (stage / "release.json").write_bytes(source.path("release.json").read_bytes())
        atomic_json(
            stage / "install.json",
            dict(
                format="explorer-install",
                version=1,
                release_sha256=digest_file(stage / "release.json"),
                artifact_ids=[a["id"] for a in selected],
            ),
        )
        storage.check()
        fsync_directory(stage)
        stage.rename(output)
        fsync_directory(output.parent)
        return dict(output=str(output), verified=result, release_id=release["release_id"])
    except BaseException:
        try:
            storage.guard()
        except (ValueError, OSError):
            pass
        else:
            shutil.rmtree(stage)
        raise


def verify_install(directory, allow_drafts=False, reserve_bytes=1024**3):
    storage = ExternalDirectory(directory, reserve_bytes)
    installed = json.loads(storage.path("install.json").read_text())
    if installed.get("format") != "explorer-install" or installed.get("version") != 1:
        raise ValueError("Unsupported installation")
    release = load_release(storage.path("release.json"), installed["release_sha256"])
    permitted = select_artifacts(release, allow_drafts=allow_drafts)
    ids = set(installed["artifact_ids"])
    selected = [a for a in permitted if a["id"] in ids]
    if len(selected) != len(ids):
        raise ValueError("Installation contains unknown artifact IDs")
    for item in selected:
        path = storage.path(item["install_path"])
        if (
            path.stat().st_size != item["content_bytes"]
            or digest_file(path) != item["content_sha256"]
        ):
            raise ValueError("Installed artifact checksum mismatch")
    return verify_installed(storage.root, release, selected)
