"""Bounded, lossless JSON inventory partitions; no HTTP or Oriol dependency.

Node records are emitted in postorder with preorder sequence/parent numbers.
Only children arrays are flattened; all other fields, including unknown nested
fields, are retained. The original JSON SHA and representation hashes have
different roles. Attaching to an existing plan requires semantic equivalence
with that plan's independently checksummed original inventory.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import ijson
from ijson.common import ObjectBuilder
from jsonschema import Draft202012Validator

from .state import digest_file

FORMAT = "pw010-inventory-parts"
PART_FORMAT = "pw010-inventory-part"
VERSION = 1
MAX_PART_BYTES = 100_000_000
FLAGS = ("schema_valid", "structure_complete", "metadata_complete", "source_stable", "complete")
TYPES = ("tree", "storage_tree", "asset", "page", "issue")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


def event(name, **details):
    print(
        json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": name, **details}),
        file=sys.stderr,
        flush=True,
    )


def fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in (22, 45, 95):
                raise
    finally:
        os.close(descriptor)


def guard_directory(path):
    """Never recreate a missing root or write to a substituted volume."""
    requested = Path(path).absolute()
    root = requested.resolve(strict=True)
    stat = root.stat()
    identity = stat.st_dev, stat.st_ino

    def check():
        current = requested.resolve(strict=True)
        stat = current.stat()
        if current != root or (stat.st_dev, stat.st_ino) != identity:
            raise ValueError("Inventory destination filesystem identity changed")
        if root.parts[:2] == ("/", "Volumes") and not os.path.ismount(Path(*root.parts[:3])):
            raise ValueError("Inventory destination volume is not mounted")

    check()
    return check


def checked_copy(source, target, expected, check=lambda: None, reserve_bytes=1024**3):
    """Publish one verified part; interrupted copies never become ready parts."""
    check()
    if target.is_symlink():
        raise ValueError("Inventory symlinks are forbidden")
    if target.exists():
        if digest_file(target) != expected:
            raise ValueError("Stored inventory member checksum mismatch")
        check()
        return
    temporary = target.with_suffix(target.suffix + ".tmp")
    if temporary.is_symlink():
        raise ValueError("Inventory symlinks are forbidden")
    hasher = hashlib.sha256()
    with Path(source).open("rb") as incoming, temporary.open("wb") as output:
        while block := incoming.read(1024 * 1024):
            check()
            if shutil.disk_usage(target.parent).free - len(block) < reserve_bytes:
                raise ValueError("Inventory filesystem reserve exhausted")
            output.write(block)
            hasher.update(block)
        output.flush()
        os.fsync(output.fileno())
    if hasher.hexdigest() != expected:
        raise ValueError("Inventory source changed during copy")
    check()
    temporary.replace(target)
    fsync_directory(target.parent)


def _item(path, prefix):
    with Path(path).open("rb") as stream:
        return next(ijson.items(stream, prefix, use_float=True), None)


def original_nodes(path, root="tree"):
    """Preserve nested extension fields without materializing any subtree."""
    stack, sequence, root_keys = [], 0, set()
    root_values = set()
    root_types = {
        "schema_version": "number",
        "metadata": "start_map",
        "tree": "start_map",
        "storage_tree": "start_map",
        "assets": "start_map",
        "pages": "start_array",
        "validation": "start_map",
        "issues": "start_array",
    }
    builder = None
    field_prefix = field_name = None
    with Path(path).open("rb") as stream:
        for prefix, action, value in ijson.parse(stream, use_float=True):
            if prefix == "" and action == "map_key":
                if value in root_keys:
                    raise ValueError("Duplicate root JSON key")
                root_keys.add(value)
            if prefix in root_types and prefix not in root_values:
                if action != root_types[prefix]:
                    raise ValueError("Invalid top-level inventory value type")
                root_values.add(prefix)
            if prefix != root and not prefix.startswith(root + "."):
                continue
            if builder is not None:
                builder.event(action, value)
                if prefix == field_prefix and action in ("end_map", "end_array"):
                    stack[-1]["data"][field_name] = builder.value
                    builder = None
                continue
            if action == "start_map" and (
                prefix == root
                or (
                    stack
                    and stack[-1].get("field") == "children"
                    and prefix == stack[-1]["prefix"] + ".children.item"
                )
            ):
                sequence += 1
                if len(stack) >= 64:
                    raise ValueError("Source hierarchy exceeds supported depth")
                stack.append(
                    {
                        "type": root,
                        "seq": sequence,
                        "parent": stack[-1]["seq"] if stack else None,
                        "prefix": prefix,
                        "data": {},
                        "keys": set(),
                    }
                )
            elif stack:
                frame = stack[-1]
                if prefix == frame["prefix"] and action == "map_key":
                    if value in frame["keys"]:
                        raise ValueError("Duplicate tree node field")
                    frame["keys"].add(value)
                    frame["field"] = value
                elif prefix == frame["prefix"] and action == "end_map":
                    stack.pop()
                    yield {key: frame[key] for key in ("type", "seq", "parent", "data")}
                elif prefix == frame["prefix"] + ".children":
                    if action == "start_array":
                        frame["data"]["children"] = []
                    elif action != "end_array":
                        raise ValueError("Node children must be an array")
                elif (
                    frame.get("field") == "children"
                    and prefix == frame["prefix"] + ".children.item"
                ):
                    raise ValueError("Node children must contain objects")
                elif prefix == frame["prefix"] + "." + str(frame.get("field")):
                    key = frame["field"]
                    if key != "children":
                        if action in ("string", "number", "boolean", "null"):
                            frame["data"][key] = value
                        elif action in ("start_map", "start_array"):
                            builder = ObjectBuilder()
                            field_prefix, field_name = prefix, key
                            builder.event(action, value)
    required = {"schema_version", "metadata", "tree", "assets", "pages", "validation", "issues"}
    if _item(path, "schema_version") == 1:
        required.add("storage_tree")
    if stack or root_keys != required:
        raise ValueError("Missing or unsupported top-level inventory fields")


def _part_records(path):
    """Validate the entire envelope/trailing JSON while streaming records."""
    keys, header, builder = set(), {}, None
    with Path(path).open("rb") as stream:
        for prefix, action, value in ijson.parse(stream, use_float=True):
            if prefix == "" and action == "map_key":
                if value in keys or value not in ("format", "version", "records"):
                    raise ValueError("Invalid inventory part envelope")
                keys.add(value)
            elif prefix in ("format", "version") and action in ("string", "number"):
                header[prefix] = value
            if prefix == "records" and action == "start_array":
                header["records"] = True
            if builder is not None:
                builder.event(action, value)
                if prefix == "records.item" and action == "end_map":
                    yield builder.value
                    builder = None
            elif prefix == "records.item":
                if action != "start_map":
                    raise ValueError("Inventory records must be objects")
                builder = ObjectBuilder()
                builder.event(action, value)
    if (
        type(header.get("version")) is not int
        or keys != {"format", "version", "records"}
        or header
        != {
            "format": PART_FORMAT,
            "version": VERSION,
            "records": True,
        }
    ):
        raise ValueError("Invalid inventory part envelope")


class Inventory:
    """One original JSON or one manifest; iterators never load a full part."""

    def __init__(self, path, manifest=False):
        requested = Path(path)
        if requested.is_symlink():
            raise ValueError("Inventory symlinks are forbidden")
        self.path = requested.resolve(strict=True)
        self.is_bundle = manifest
        self.manifest = None
        if manifest:
            if self.path.stat().st_size > 4 * 1024**2:
                raise ValueError("Inventory manifest exceeds size limit")
            self.manifest_sha256 = digest_file(self.path)
            self.manifest = json.loads(self.path.read_text())
            m = self.manifest
            if not isinstance(m, dict) or set(m) != {
                "format",
                "version",
                "source_sha256",
                "source_bytes",
                "header",
                "target_part_bytes",
                "parts",
                "semantic_signature",
            }:
                raise ValueError("Invalid inventory manifest fields")
            if not isinstance(m["header"], dict) or set(m["header"]) != {
                "schema_version",
                "metadata",
                "validation",
            }:
                raise ValueError("Invalid inventory manifest header")
            if (
                m.get("format") != FORMAT
                or type(m.get("version")) is not int
                or m["version"] != VERSION
            ):
                raise ValueError("Unsupported inventory manifest")
            if not re.fullmatch(r"[0-9a-f]{64}", str(m.get("source_sha256"))):
                raise ValueError("Invalid original inventory SHA-256")
            if type(m.get("source_bytes")) is not int or m["source_bytes"] <= 0:
                raise ValueError("Invalid original inventory size")
            if (
                type(m.get("target_part_bytes")) is not int
                or not 256 <= m["target_part_bytes"] <= MAX_PART_BYTES
            ):
                raise ValueError("Invalid inventory target part size")
            if not isinstance(m.get("parts"), list) or not m["parts"]:
                raise ValueError("Inventory manifest has no parts")
            for number, part in enumerate(m["parts"], 1):
                if not isinstance(part, dict):
                    raise ValueError("Invalid inventory part descriptor")
                if part.get("path") != f"part-{number:05d}.json":
                    raise ValueError("Unsafe, duplicate or unordered inventory part")
                if (
                    type(part.get("bytes")) is not int
                    or not 0 < part["bytes"] <= MAX_PART_BYTES
                    or not re.fullmatch(r"[0-9a-f]{64}", str(part.get("sha256")))
                ):
                    raise ValueError("Invalid inventory part size or hash")
                counts = part.get("counts")
                if (
                    not isinstance(counts, dict)
                    or not counts
                    or set(counts) - set(TYPES)
                    or any(type(n) is not int or n <= 0 for n in counts.values())
                ):
                    raise ValueError("Invalid inventory part counters")
            self.header = m["header"]
            self.sha256 = m["source_sha256"]
            self.source_bytes = m["source_bytes"]
        else:
            self.sha256 = digest_file(self.path)
            self.source_bytes = self.path.stat().st_size
            self.header = {
                key: _item(self.path, key) for key in ("schema_version", "metadata", "validation")
            }
        version = self.header.get("schema_version")
        if type(version) is not int or version not in (1, 2):
            raise ValueError("Only PW-010 JSON schema v1/v2 is supported")
        self.schema = json.loads(
            Path(__file__).with_name(f"pw010-v{version}.schema.json").read_text()
        )
        for key in ("metadata", "validation"):
            Draft202012Validator(self.schema["properties"][key]).validate(self.header[key])

    def member(self, part):
        path = self.path.parent / part["path"]
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.path.parent:
            raise ValueError("Inventory part missing or unsafe")
        return path

    def records(self, kind, skip=0):
        if self.is_bundle:
            for part in self.manifest["parts"]:
                count = part["counts"].get(kind, 0)
                if not count:
                    continue
                if skip >= count:
                    skip -= count
                    continue
                for record in _part_records(self.member(part)):
                    if record.get("type") == kind:
                        if skip:
                            skip -= 1
                        else:
                            yield record
        elif kind in ("tree", "storage_tree"):
            yield from original_nodes(self.path, kind)
        elif kind == "asset":
            with self.path.open("rb") as stream:
                for key, value in ijson.kvitems(stream, "assets", use_float=True):
                    yield {"type": kind, "id": key, "data": value}
        else:
            prefix = "pages.item" if kind == "page" else "issues.item"
            with self.path.open("rb") as stream:
                for value in ijson.items(stream, prefix, use_float=True):
                    yield {"type": kind, "data": value}

    def kinds(self):
        return (
            ("tree", "storage_tree", "asset", "page", "issue")
            if (self.header["schema_version"] == 1)
            else ("tree", "asset", "page", "issue")
        )

    def _check_record(self, record, validators):
        if not isinstance(record, dict) or not isinstance(record.get("data"), dict):
            raise ValueError("Invalid inventory record payload")
        kind = record.get("type")
        if kind not in self.kinds():
            raise ValueError("Unknown inventory record type")
        expected = {"type", "data"} | (
            {"seq", "parent"}
            if kind in ("tree", "storage_tree")
            else {"id"}
            if kind == "asset"
            else set()
        )
        if set(record) != expected:
            raise ValueError("Invalid inventory record fields")
        if kind in ("tree", "storage_tree"):
            seq, parent = record["seq"], record["parent"]
            if (
                type(seq) is not int
                or seq <= 0
                or (parent is not None and (type(parent) is not int or not 0 < parent < seq))
            ):
                raise ValueError("Invalid inventory node sequence/parent")
            if "children" in record["data"] and record["data"]["children"] != []:
                raise ValueError("Partitioned children must be flattened")
        if kind == "asset" and not isinstance(record["id"], str):
            raise ValueError("Invalid inventory asset key")
        if validators is not None:
            validators[kind].validate(record["data"])

    def scan(self, sink=None, check=lambda: None, validate_schema=False):
        """Read every record and verify representation, schema, counts and digest."""
        hashes = {kind: hashlib.sha256() for kind in self.kinds()}
        counts = Counter()
        measured = Counter()
        validators = {
            kind: Draft202012Validator(
                self.schema["properties"]["issues"]["items"]
                if kind == "issue"
                else {
                    "$defs": self.schema["$defs"],
                    "$ref": "#/$defs/" + ("node" if kind in ("tree", "storage_tree") else kind),
                }
            )
            for kind in self.kinds()
        }

        def accept(record):
            kind = record.get("type")
            self._check_record(record, validators if validate_schema else None)
            hashes[kind].update(canonical(record) + b"\n")
            counts[kind] += 1
            if kind == "asset":
                data = record["data"]
                measured["known_unique_size_bytes"] += data["size_bytes"] or 0
                measured["unknown_sizes_count"] += data["size_bytes"] is None
                measured["file_placements_count"] += len(data["occurrences"])
            if sink:
                sink(record)
            if sum(counts.values()) % 5000 == 0:
                check()
            if sum(counts.values()) % 50000 == 0:
                event("inventory_scan_progress", records=sum(counts.values()))

        if self.is_bundle:
            for number, part in enumerate(self.manifest["parts"], 1):
                check()
                path = self.member(part)
                if path.stat().st_size != part["bytes"] or digest_file(path) != part["sha256"]:
                    raise ValueError("Inventory part checksum mismatch")
                part_counts = Counter()
                for record in _part_records(path):
                    accept(record)
                    part_counts[record["type"]] += 1
                if dict(part_counts) != part["counts"]:
                    raise ValueError("Inventory part record count mismatch")
                event("inventory_part_verified", part=number, total=len(self.manifest["parts"]))
        else:
            for kind in self.kinds():
                event("inventory_scan", kind=kind)
                for record in self.records(kind):
                    accept(record)
        if all(self.header["validation"].get(key) is True for key in FLAGS) and (
            not counts["asset"] or counts["issue"] or measured["unknown_sizes_count"]
        ):
            raise ValueError("Certified inventory is empty or reports unresolved data")
        measured["unique_files_count"] = counts["asset"]
        measured["pages_count"] = counts["page"]
        measured["issues_count"] = counts["issue"]
        if any(self.header["validation"][key] != value for key, value in measured.items()):
            raise ValueError("Inventory counters do not match its records")
        signature = {
            "header_sha256": hashlib.sha256(canonical(self.header)).hexdigest(),
            "counts": dict(counts),
            "records_sha256": {kind: value.hexdigest() for kind, value in hashes.items()},
        }
        if self.is_bundle:
            if digest_file(self.path) != self.manifest_sha256:
                raise ValueError("Inventory manifest changed during validation")
            if signature != self.manifest.get("semantic_signature"):
                raise ValueError("Inventory semantic signature mismatch")
        if not self.is_bundle and digest_file(self.path) != self.sha256:
            raise ValueError("Original inventory changed during partitioning")
        return signature


def split_inventory(source, output, target_bytes=75_000_000, reserve_bytes=1024**3):
    source = Inventory(source)
    if not all(source.header["validation"].get(key) is True for key in FLAGS):
        raise ValueError("Partitioning requires a certified complete inventory")
    if type(target_bytes) is not int or not 256 <= target_bytes <= MAX_PART_BYTES:
        raise ValueError("Target part size must be between 256 and 100000000 bytes")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Partition output must not exist; original data is never replaced")
    check = guard_directory(output.parent)
    if shutil.disk_usage(output.parent).free - source.source_bytes * 2 < reserve_bytes:
        raise ValueError("Inventory filesystem reserve is unavailable")
    prefix = encoded({"format": PART_FORMAT, "version": VERSION})[:-1] + b',"records":[\n'
    suffix = b"\n]}\n"
    parts, stream, part_counts, part_size = [], None, Counter(), 0
    with tempfile.TemporaryDirectory(prefix=".pw010-parts-", dir=output.parent) as directory:
        stage = Path(directory) / "inventory"
        stage.mkdir()

        def finish():
            nonlocal stream
            if stream is None:
                return
            stream.write(suffix)
            stream.flush()
            os.fsync(stream.fileno())
            name = Path(stream.name).name
            stream.close()
            stream = None
            path = stage / name
            parts.append(
                {
                    "path": name,
                    "bytes": path.stat().st_size,
                    "sha256": digest_file(path),
                    "counts": dict(part_counts),
                }
            )
            event("inventory_part_written", part=len(parts), bytes=path.stat().st_size)

        def write(record):
            nonlocal stream, part_counts, part_size
            data = encoded(record)
            if len(prefix) + len(data) + len(suffix) > MAX_PART_BYTES:
                raise ValueError("One inventory record exceeds the 100 MB part limit")
            if stream and part_size + 2 + len(data) + len(suffix) > target_bytes:
                finish()
            if stream is None:
                check()
                stream = (stage / f"part-{len(parts) + 1:05d}.json").open("wb")
                stream.write(prefix)
                part_size, part_counts = len(prefix), Counter()
            if part_counts:
                stream.write(b",\n")
                part_size += 2
            stream.write(data)
            part_size += len(data)
            part_counts[record["type"]] += 1

        def storage_check():
            check()
            if shutil.disk_usage(output.parent).free < reserve_bytes + MAX_PART_BYTES:
                raise ValueError("Inventory filesystem reserve exhausted")

        try:
            signature = source.scan(write, storage_check, validate_schema=True)
            finish()
        finally:
            if stream:
                stream.close()
        manifest = {
            "format": FORMAT,
            "version": VERSION,
            "source_sha256": source.sha256,
            "source_bytes": source.source_bytes,
            "header": source.header,
            "target_part_bytes": target_bytes,
            "parts": parts,
            "semantic_signature": signature,
        }
        with (stage / "manifest.json").open("wb") as saved:
            saved.write(encoded(manifest) + b"\n")
            saved.flush()
            os.fsync(saved.fileno())
        # Independent read-back proves flattening/serialization retained every record.
        Inventory(stage / "manifest.json", manifest=True).scan(check=storage_check)
        check()
        fsync_directory(stage)
        if output.exists() or output.is_symlink():
            raise ValueError("Partition output appeared during export")
        stage.rename(output)
        fsync_directory(output.parent)
    return {
        "manifest": str(output / "manifest.json"),
        "source_sha256": source.sha256,
        "parts": len(parts),
        "bytes": sum(part["bytes"] for part in parts),
        "counts": signature["counts"],
    }


def pin_bundle(archive, source, reserve_bytes=1024**3, validated=False):
    """Resume copying verified parts; publish the local manifest last."""
    from .archive_catalog import CONTROL, log_event

    if not validated:
        source.scan(check=archive.check)
    directory = archive.path(f"{CONTROL}/inputs/bundles/{source.manifest_sha256}")
    archive.make_parent(directory / "manifest.json")
    for number, part in enumerate(source.manifest["parts"], 1):
        archive.check()
        checked_copy(
            source.member(part),
            directory / part["path"],
            part["sha256"],
            archive.check,
            reserve_bytes,
        )
        log_event(
            archive, "inventory_part_pinned", part=number, total=len(source.manifest["parts"])
        )
    destination = directory / "manifest.json"
    checked_copy(source.path, destination, source.manifest_sha256, archive.check, reserve_bytes)
    # The copied representation retains its own hash; origin identity stays unchanged.
    saved = Inventory(destination, manifest=True)
    if saved.manifest != source.manifest:
        raise ValueError("Stored inventory manifest mismatch")
    return saved


def archive_inventory(archive):
    from .archive_catalog import CONTROL

    bundle = archive.meta("source_bundle")
    if bundle:
        path = archive.path(bundle["path"])
        if digest_file(path) != bundle["manifest_sha256"]:
            raise ValueError("Stored inventory manifest checksum mismatch")
        return Inventory(path, manifest=True)
    return Inventory(archive.path(f"{CONTROL}/inputs/{archive.meta('source_sha256')}.json"))


def attach_bundle(archive, source, reserve_bytes=1024**3):
    """Attach an equivalent representation without rewriting any downloaded state."""
    from .archive_catalog import log_event, report

    archive.ready()
    if source.sha256 != archive.meta("source_sha256"):
        raise ValueError("Partitioned inventory belongs to a different original dump")
    previous = archive_inventory(archive)
    event("inventory_equivalence_start")
    previous_signature = previous.scan(check=archive.check)
    if (
        previous_signature != source.scan(check=archive.check)
        or previous.source_bytes != source.source_bytes
    ):
        raise ValueError("Partitioned inventory differs from the immutable original")
    if previous.sha256 != archive.meta("source_sha256"):
        raise ValueError("Stored original inventory checksum mismatch")
    if previous.is_bundle and previous.manifest == source.manifest:
        return report(archive)
    saved = pin_bundle(archive, source, reserve_bytes, validated=True)
    with archive.db:
        archive.set_meta(
            "source_bundle",
            {
                "path": str(saved.path.relative_to(archive.root)),
                "manifest_sha256": saved.manifest_sha256,
            },
        )
        archive.set_meta("last_full_audit", None)
        archive.set_meta("pilot_audit", None)
    log_event(
        archive,
        "inventory_bundle_attached",
        parts=len(saved.manifest["parts"]),
        original_json_retained=True,
    )
    return report(archive)
