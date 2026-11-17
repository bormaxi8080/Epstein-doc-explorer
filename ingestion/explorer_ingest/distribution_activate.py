"""Verify an immutable installation before switching a relative Viewer pointer."""

import json
import os
import uuid
from pathlib import Path

from .distribution_contracts import load_release, load_workspace, safe_path
from .distribution_install import verify_install
from .inventory_parts import fsync_directory
from .state import atomic_json, digest_file


def activate(workspace, installation, allow_drafts=False):
    config, storage = load_workspace(workspace)
    releases = storage.path(config["paths"]["releases"])
    requested = Path(installation).absolute()
    if requested.is_symlink():
        raise ValueError("Activation cannot select a symlink installation")
    installation = requested.resolve(strict=True)
    if installation.is_symlink() or not installation.is_dir() or installation.parent != releases:
        raise ValueError(
            "Activation needs an immutable installation directly under workspace releases"
        )
    verify_install(installation, allow_drafts, storage.reserve_bytes)
    release = load_release(installation / "release.json")
    installed = json.loads((installation / "install.json").read_text())["artifact_ids"]
    viewers = [a for a in release["artifacts"] if a["id"] in installed and a["role"] == "viewer-db"]
    if len(viewers) != 1:
        raise ValueError("Activation requires exactly one Viewer database")
    viewer = safe_path(installation, viewers[0]["install_path"]).parent
    current = releases / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("Existing current path is not a release pointer")
    old = os.readlink(current) if current.is_symlink() else None
    if old and (Path(old).is_absolute() or not current.resolve().is_relative_to(releases)):
        raise ValueError("Existing current pointer is outside releases")
    target = str(viewer.relative_to(releases))
    temporary = releases / (".current-" + uuid.uuid4().hex)
    storage.check()
    try:
        temporary.symlink_to(target, target_is_directory=True)
        temporary.replace(current)
        fsync_directory(releases)
    finally:
        temporary.unlink(missing_ok=True)
    result = dict(
        release_id=release["release_id"],
        release_sha256=digest_file(installation / "release.json"),
        previous=old,
        current=target,
        restart_required=True,
    )
    atomic_json(releases / "activation.json", result)
    return result
