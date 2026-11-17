"""Portable release contracts and pinned external directories; no network I/O."""

import json
import re
import shutil
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

from . import __version__
from .inventory_parts import guard_directory

FORMAT = "epstein-explorer-release"
GROUPS = (
    "inventory-full",
    "inventory-parts",
    "inventory-db",
    "baseline",
    "viewer",
    "pilot",
    "catalogue",
)
PROFILES = {
    "viewer": ("viewer", "catalogue"),
    "catalogue": ("catalogue",),
    "pipeline": ("inventory-parts", "inventory-db", "catalogue"),
    "inventory-full": ("inventory-full", "inventory-db"),
    "pilot": ("pilot",),
}
ROLES = (
    "inventory-full",
    "inventory-manifest",
    "inventory-part",
    "inventory-db",
    "baseline-db",
    "viewer-db",
    "viewer-clusters",
    "viewer-manifest",
    "viewer-text",
    "pilot-original",
    "pilot-selection",
    "pilot-ocr",
    "provenance",
    "catalog-db",
)
SCHEMA = json.loads(Path(__file__).with_name("distribution-v1.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def relative_name(value):
    if not isinstance(value, str) or not value or len(value.encode()) > 1024:
        raise ValueError("Invalid portable path")
    path = PurePosixPath(value)
    if (
        not path.parts
        or str(path) != value
        or path.is_absolute()
        or any(x in (".", "..") for x in path.parts)
        or any(
            re.search(r'[\\:\x00-\x1f<>"|?*]', x) or x.endswith((".", " ")) or len(x.encode()) > 255
            for x in path.parts
        )
    ):
        raise ValueError("Unsafe portable path")
    return path


def safe_path(root, relative):
    path = Path(root)
    for part in relative_name(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Package paths cannot contain symlinks")
    if not path.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("Package path escaped its root")
    return path


def version_tuple(value):
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value):
        raise ValueError("A stable semantic version is required")
    return tuple(map(int, value.split(".")))


def validate_release(value, app_version=__version__):
    VALIDATOR.validate(value)
    if version_tuple(value["minimum_app_version"]) > version_tuple(app_version):
        raise ValueError("Release requires a newer application")
    ids, paths, installed = set(), set(), set()
    for item in value["artifacts"]:
        if value["visibility"] == "public" and (
            item["group"] == "pilot" or item["role"].startswith("pilot-")
        ):
            raise ValueError("Pilot review artifacts require a private release")
        relative_name(item["path"])
        relative_name(item["install_path"])
        reserved = {
            "release.json",
            "install.json",
            "verification.json",
            "sha256sums",
            "readme.md",
            "sources.json",
            "current",
        }
        for name in (item["path"], item["install_path"]):
            if name.split("/")[0].startswith(".") or name.casefold() in reserved:
                raise ValueError("Artifact path collides with package control files")
        # Case-folding also protects case-insensitive macOS/Windows filesystems.
        if (
            item["id"] in ids
            or item["path"].casefold() in paths
            or item["install_path"].casefold() in installed
        ):
            raise ValueError("Duplicate artifact identity or path")
        ids.add(item["id"])
        paths.add(item["path"].casefold())
        installed.add(item["install_path"].casefold())
    for names in (paths, installed):
        for name in names:
            if any(
                str(parent) in names for parent in PurePosixPath(name).parents if str(parent) != "."
            ):
                raise ValueError("Artifact file/directory collision")
    return value


def load_release(path, expected_sha256=None):
    from .state import digest_file

    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 8 * 1024**2:
        raise ValueError("Invalid release descriptor")
    if expected_sha256 and digest_file(path) != expected_sha256:
        raise ValueError("Release descriptor checksum mismatch")
    return validate_release(json.loads(path.read_text()))


class ExternalDirectory:
    def __init__(self, root, reserve_bytes=1024**3):
        requested = Path(root)
        if not requested.is_absolute() or not requested.is_dir() or requested.is_symlink():
            raise ValueError("An existing absolute external directory is required")
        checkout = Path(__file__).resolve().parents[2]
        self.root = requested.resolve(strict=True)
        if self.root == checkout or checkout in self.root.parents:
            raise ValueError("Data must stay outside the source checkout")
        if reserve_bytes < 0:
            raise ValueError("Invalid free-space reserve")
        self.reserve_bytes = reserve_bytes
        self.guard = guard_directory(requested)
        self.check()

    def check(self, additional=0):
        self.guard()
        if additional < 0 or shutil.disk_usage(self.root).free < self.reserve_bytes + additional:
            raise ValueError("External disk reserve exhausted")

    def path(self, name):
        self.check()
        return safe_path(self.root, name)

    def mkdir(self, name):
        self.check()
        path = self.path(name)
        current = self.root
        for part in path.relative_to(self.root).parts:
            self.check()
            current = current / part
            current.mkdir(exist_ok=True)
            if current.is_symlink() or current.stat().st_dev != self.root.stat().st_dev:
                raise ValueError("Data child filesystem changed")
        return path


def load_workspace(path):
    value = json.loads(Path(path).read_text())
    if value.get("format") != "explorer-workspace" or value.get("version") != 1:
        raise ValueError("Unsupported workspace configuration")
    external = ExternalDirectory(value["root"], value.get("reserve_bytes", 5 * 1024**3))
    names = value.get("paths", {})
    required = {"packages", "inventory", "downloads", "corpus", "releases", "scratch"}
    if set(names) != required or len(set(names.values())) != len(names):
        raise ValueError("Workspace needs distinct paths for every role")
    folded = {name.casefold() for name in names.values()}
    if len(folded) != len(names) or any(
        str(parent) in folded
        for name in folded
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    ):
        raise ValueError("Workspace roles cannot overlap")
    for name in names.values():
        external.path(name)
    return value, external
