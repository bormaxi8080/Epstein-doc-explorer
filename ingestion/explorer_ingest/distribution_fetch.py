"""Pinned release acquisition: bounded HTTP, strong-validator resume, no implicit I/O."""

import fcntl
import hashlib
import http.client
import ipaddress
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import certifi

from .distribution_contracts import ExternalDirectory, load_release
from .distribution_release import select_artifacts
from .state import atomic_json, digest_file


class Paused(ValueError):
    def __init__(self, reason, retry_after=30):
        super().__init__(reason)
        self.retry_after = min(300, max(0, retry_after))


class HTTPClient:
    def __init__(self, allow_hosts=(), allow_loopback=False, timeout=60):
        if not 1 <= timeout <= 300:
            raise ValueError("Invalid HTTP timeout")
        self.hosts, self.loopback, self.timeout = set(allow_hosts), allow_loopback, timeout
        self.context = ssl.create_default_context(
            cafile=os.environ.get("SSL_CERT_FILE") or certifi.where()
        )

    def validate(self, url):
        p = urlsplit(url)
        if p.username or p.password or p.fragment or not p.hostname:
            raise ValueError("Invalid source URL")
        try:
            loopback = ipaddress.ip_address(p.hostname).is_loopback
        except ValueError:
            loopback = p.hostname == "localhost"
        if self.loopback and loopback and p.scheme == "http":
            return
        defaults = {
            "www.googleapis.com",
            "drive.google.com",
            "drive.usercontent.google.com",
            "github.com",
            "objects.githubusercontent.com",
            "release-assets.githubusercontent.com",
        }
        if p.scheme != "https" or not (
            p.hostname in self.hosts | defaults or p.hostname.endswith(".googleusercontent.com")
        ):
            raise ValueError("Source host/scheme not admitted")

    def open(self, url, headers=None):
        self.validate(url)
        client = self

        class Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                client.validate(newurl)
                if urlsplit(req.full_url).scheme == "https" and urlsplit(newurl).scheme != "https":
                    raise ValueError("HTTPS downgrade rejected")
                result = super().redirect_request(req, fp, code, msg, headers, newurl)
                if (urlsplit(req.full_url).scheme, urlsplit(req.full_url).netloc) != (
                    urlsplit(newurl).scheme,
                    urlsplit(newurl).netloc,
                ):
                    for name in ("Authorization", "X-goog-drive-resource-keys", "Cookie"):
                        result.remove_header(name)
                return result

        opener = urllib.request.build_opener(
            Redirect(), urllib.request.HTTPSHandler(context=self.context)
        )
        return opener.open(
            urllib.request.Request(url, headers={"Accept-Encoding": "identity", **(headers or {})}),
            timeout=self.timeout,
        )


def locator(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid source locator")
    if set(value) == {"url"}:
        if not isinstance(value["url"], str) or len(value["url"]) > 16384:
            raise ValueError("Invalid source URL")
        return value["url"], {}
    if set(value) <= {"drive_file_id", "resource_key"} and "drive_file_id" in value:
        file_id = value["drive_file_id"]
        if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", file_id):
            raise ValueError("Invalid Drive file ID")
        headers = {}
        if os.environ.get("EXPLORER_DRIVE_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["EXPLORER_DRIVE_TOKEN"]
        if value.get("resource_key"):
            key = value["resource_key"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", key):
                raise ValueError("Invalid Drive resource key")
            headers["X-Goog-Drive-Resource-Keys"] = file_id + "/" + key
        return "https://www.googleapis.com/drive/v3/files/" + file_id + "?alt=media", headers
    raise ValueError("A source needs one HTTPS URL or a Drive file ID")


@contextmanager
def writer(storage):
    storage.mkdir(".download-state")
    with storage.path(".download-state/writer.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another release download owns this directory") from None
        yield


def download_one(storage, item, source, client, retry_delay=30):
    target = storage.path(item["path"])
    if target.exists():
        if target.stat().st_size != item["bytes"] or digest_file(target) != item["sha256"]:
            raise ValueError(
                "Existing artifact is corrupt; preserve it and choose a new destination"
            )
        return
    url, headers = locator(source)
    client.validate(url)
    identity = hashlib.sha256(url.encode()).hexdigest()
    name = ".download-state/" + item["id"]
    partial, checkpoint = storage.path(name + ".part"), storage.path(name + ".json")
    if checkpoint.exists() and checkpoint.stat().st_size > 16384:
        raise ValueError("Partial checkpoint exceeds its size bound")
    meta = json.loads(checkpoint.read_text()) if checkpoint.exists() else {}
    offset, digest = 0, hashlib.sha256()
    if meta and (type(meta.get("bytes")) is not int or not 0 <= meta["bytes"] <= item["bytes"]):
        raise ValueError("Invalid partial checkpoint length")
    if meta and meta.get("artifact_sha256") == item["sha256"] and meta.get("source") == identity:
        if meta.get("not_before", 0) > time.time():
            raise Paused(
                "Source cooldown has not elapsed", int(meta["not_before"] - time.time()) + 1
            )
        if partial.exists() and partial.stat().st_size >= meta["bytes"]:
            with partial.open("rb") as f:
                remaining = meta["bytes"]
                while remaining:
                    block = f.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ValueError("Partial prefix changed during verification")
                    digest.update(block)
                    remaining -= len(block)
            if digest.hexdigest() == meta["prefix_sha256"]:
                offset = meta["bytes"]
            else:
                partial.rename(storage.path(name + ".corrupt-" + uuid.uuid4().hex))
    if offset == item["bytes"] and digest.hexdigest() == item["sha256"]:
        parent = str(Path(item["path"]).parent)
        if parent != ".":
            storage.mkdir(parent)
        if not partial.exists():
            with partial.open("xb"):
                pass
        partial.replace(target)
        checkpoint.unlink(missing_ok=True)
        return
    etag = meta.get("etag")
    if offset and isinstance(etag, str) and etag.startswith('"') and etag.endswith('"'):
        headers.update(Range=f"bytes={offset}-", **{"If-Range": etag})
    else:
        offset, digest = 0, hashlib.sha256()
    meta = dict(
        artifact_sha256=item["sha256"],
        source=identity,
        bytes=offset,
        prefix_sha256=digest.hexdigest(),
        etag=etag if offset else None,
    )
    storage.check(item["bytes"] - offset)
    deadline = time.monotonic() + 3600
    try:
        with client.open(url, headers) as response:
            content_type = response.headers.get("Content-Type", "").lower()
            if (
                "text/html" in content_type
                or response.headers.get("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("Login/HTML or encoded HTTP response rejected")
            status = response.status
            if status == 206:
                expected = f"bytes {offset}-{item['bytes'] - 1}/{item['bytes']}"
                if (
                    not offset
                    or response.headers.get("Content-Range") != expected
                    or response.headers.get("ETag") != etag
                ):
                    raise ValueError("Invalid ranged response")
            elif status == 200:
                offset, digest = 0, hashlib.sha256()
            else:
                raise ValueError("Unexpected source response")
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != item["bytes"] - offset:
                raise ValueError("HTTP transfer size differs from the release")
            new_etag = response.headers.get("ETag")
            meta.update(etag=new_etag if new_etag and not new_etag.startswith("W/") else None)
            with partial.open("r+b" if partial.exists() else "w+b") as output:
                output.truncate(offset)
                output.seek(offset)
                while block := response.read1(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise Paused("Artifact transfer window elapsed; resume retained prefix")
                    if offset + len(block) > item["bytes"]:
                        raise ValueError("HTTP body exceeds release size")
                    if offset == 0 and block.lstrip().lower().startswith(
                        (b"<!doctype html", b"<html")
                    ):
                        raise ValueError("HTML response disguised as artifact")
                    storage.check(len(block))
                    output.write(block)
                    output.flush()
                    os.fsync(output.fileno())
                    offset += len(block)
                    digest.update(block)
                    meta.update(bytes=offset, prefix_sha256=digest.hexdigest())
                    atomic_json(checkpoint, meta)
        if offset != item["bytes"]:
            raise Paused("Interrupted artifact body", retry_delay)
        if digest.hexdigest() != item["sha256"]:
            partial.rename(storage.path(name + ".corrupt-" + uuid.uuid4().hex))
            checkpoint.unlink(missing_ok=True)
            raise ValueError("Downloaded artifact checksum mismatch")
        parent = str(Path(item["path"]).parent)
        if parent != ".":
            storage.mkdir(parent)
        storage.check()
        partial.replace(target)
        checkpoint.unlink(missing_ok=True)
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 408, 416, 429, 500, 502, 503, 504):
            delay = exc.headers.get("Retry-After", "")
            if delay.isdigit():
                delay = int(delay)
            elif delay:
                try:
                    delay = max(0, int(parsedate_to_datetime(delay).timestamp() - time.time()))
                except (TypeError, ValueError, OverflowError):
                    delay = retry_delay
            else:
                delay = 300 if exc.code == 403 else retry_delay
            if exc.code == 416:
                meta.update(bytes=0, prefix_sha256=hashlib.sha256().hexdigest(), etag=None)
            pause = Paused(
                f"HTTP {exc.code}; permissions/quota/source availability require retry", delay
            )
            meta["not_before"] = time.time() + pause.retry_after
            storage.check()
            atomic_json(checkpoint, meta)
            raise pause from None
        raise ValueError(f"HTTP {exc.code}; artifact unavailable") from None
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException, OSError) as exc:
        storage.check()
        meta["not_before"] = time.time() + retry_delay
        atomic_json(checkpoint, meta)
        raise Paused("Transport interrupted; downloaded prefix retained", retry_delay) from exc
    except Paused as exc:
        meta["not_before"] = time.time() + exc.retry_after
        storage.check()
        atomic_json(checkpoint, meta)
        raise


def fetch_release(
    manifest,
    expected_sha256,
    sources_path,
    destination,
    groups=None,
    allow_drafts=False,
    allow_hosts=(),
    allow_loopback=False,
    timeout=60,
    reserve_bytes=1024**3,
    retry_delay=30,
):
    if not expected_sha256:
        raise ValueError("Fetch requires the publisher-pinned release SHA-256")
    release = load_release(manifest, expected_sha256)
    selected = select_artifacts(release, groups, allow_drafts)
    if Path(sources_path).stat().st_size > 8 * 1024**2:
        raise ValueError("Source map exceeds its size bound")
    sources = json.loads(Path(sources_path).read_text())
    if (
        sources.get("format") != "explorer-release-sources"
        or sources.get("version") != 1
        or sources.get("release_sha256") != expected_sha256
    ):
        raise ValueError("Source map belongs to a different release")
    for item in selected:
        locator(sources["artifacts"][item["id"]])
    storage = ExternalDirectory(destination, reserve_bytes)
    stored = storage.path("release.json")
    if stored.exists():
        if digest_file(stored) != expected_sha256:
            raise ValueError("Destination belongs to another release")
    else:
        if any(storage.root.iterdir()):
            raise ValueError("Initial release destination must be empty")
        raw = Path(manifest).read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise ValueError("Descriptor changed before publication")
        temporary = storage.path(".release-" + uuid.uuid4().hex + ".tmp")
        with temporary.open("xb") as outgoing:
            outgoing.write(raw)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        temporary.replace(stored)
    client = HTTPClient(allow_hosts, allow_loopback, timeout)
    completed = 0
    with writer(storage):
        for item in selected:
            download_one(storage, item, sources["artifacts"][item["id"]], client, retry_delay)
            completed += 1
    return dict(
        release_id=release["release_id"],
        downloaded=completed,
        profile_complete=True,
        archive_processing_complete=False,
    )
