import hashlib
import json
import os
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from explorer_ingest.distribution_fetch import HTTPClient, Paused, fetch_release
from explorer_ingest.distribution_install import import_release, verify_install
from explorer_ingest.distribution_release import build_release
from explorer_ingest.state import digest_file


class FetchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.body = b"".join(hashlib.sha256(str(i).encode()).digest() for i in range(4096))
        original = self.root / "source.bin"
        original.write_bytes(self.body)
        recipe = dict(
            release_id="synthetic-http",
            visibility="public",
            inventory=dict(
                snapshot_id="synthetic", source_sha256="a" * 64, unique_assets=0, complete=False
            ),
            artifacts=[
                dict(
                    id="one",
                    group="viewer",
                    role="provenance",
                    source=str(original),
                    path="files/source.gz",
                    install_path="data/source.bin",
                    encoding="gzip",
                )
            ],
        )
        recipe_path = self.root / "recipe.json"
        recipe_path.write_text(json.dumps(recipe))
        build_release(recipe_path, self.root / "package", reserve_bytes=0)
        self.manifest = self.root / "package/release.json"
        self.sha = digest_file(self.manifest)
        self.payload = (self.root / "package/files/source.gz").read_bytes()
        self.mode = "normal"
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append(
                    dict(
                        path=self.path,
                        range=self.headers.get("Range"),
                        if_range=self.headers.get("If-Range"),
                        auth=self.headers.get("Authorization"),
                    )
                )
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", owner.redirect_url)
                    self.end_headers()
                    return
                if owner.mode == "rate":
                    self.send_response(429)
                    self.send_header("Retry-After", "3600")
                    self.end_headers()
                    return
                if owner.mode == "html":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html>login</html>")
                    return
                offset = (
                    int(self.headers["Range"].split("=")[1].split("-")[0])
                    if self.headers.get("Range")
                    else 0
                )
                status = 206 if offset and owner.mode != "ignore" else 200
                if status == 200:
                    offset = 0
                self.send_response(status)
                self.send_header("ETag", '"synthetic-v1"')
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(owner.payload) - offset))
                if status == 206:
                    self.send_header(
                        "Content-Range",
                        f"bytes {offset + 1 if owner.mode == 'bad-range' else offset}-{len(owner.payload) - 1}/{len(owner.payload)}",
                    )
                self.end_headers()
                if owner.mode == "disconnect":
                    self.wfile.write(owner.payload[:17000])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.wfile.write(owner.payload[offset:])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.dest = self.root / "download"
        self.dest.mkdir()
        self.sources = self.root / "sources.json"
        self.set_source(dict(url=self.url + "/payload"))

    def set_source(self, source):
        self.sources.write_text(
            json.dumps(
                dict(
                    format="explorer-release-sources",
                    version=1,
                    release_sha256=self.sha,
                    artifacts=dict(one=source),
                )
            )
        )

    def fetch(self):
        return fetch_release(
            self.manifest,
            self.sha,
            self.sources,
            self.dest,
            allow_loopback=True,
            reserve_bytes=0,
            retry_delay=0,
        )

    def test_disconnect_then_range_and_relocation_preserve_content(self):
        self.mode = "disconnect"
        with self.assertRaises(Paused):
            self.fetch()
        self.mode = "normal"
        self.fetch()
        self.assertTrue(self.requests[-1]["range"].startswith("bytes=17000-"))
        self.assertEqual(self.requests[-1]["if_range"], '"synthetic-v1"')
        output = self.root / "relocated"
        import_release(self.dest, output, reserve_bytes=0, expected_sha256=self.sha)
        self.assertEqual((output / "data/source.bin").read_bytes(), self.body)
        self.assertEqual(verify_install(output, reserve_bytes=0)["artifacts"], 1)
        (output / "data/source.bin").write_bytes(b"changed")
        with self.assertRaises(ValueError):
            verify_install(output, reserve_bytes=0)

    def test_200_fallback_and_bad_range_never_concatenate_versions(self):
        self.mode = "disconnect"
        with self.assertRaises(Paused):
            self.fetch()
        self.mode = "bad-range"
        with self.assertRaisesRegex(ValueError, "ranged"):
            self.fetch()
        self.mode = "ignore"
        self.fetch()
        self.assertEqual(
            digest_file(self.dest / "files/source.gz"),
            digest_file(self.root / "package/files/source.gz"),
        )

    def test_html_and_cooldown_never_mark_ready(self):
        self.mode = "html"
        with self.assertRaises(ValueError):
            self.fetch()
        self.mode = "rate"
        with self.assertRaises(Paused) as caught:
            self.fetch()
        self.assertEqual(caught.exception.retry_after, 300)
        calls = len(self.requests)
        with self.assertRaises(Paused):
            self.fetch()
        self.assertEqual(len(self.requests), calls)
        self.assertFalse((self.dest / "files/source.gz").exists())

    def test_cross_origin_redirect_strips_authorization(self):
        owner = self

        class Other(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.redirect_auth = self.headers.get("Authorization")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"synthetic")

        other = ThreadingHTTPServer(("127.0.0.1", 0), Other)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(other.server_close)
        self.addCleanup(other.shutdown)
        self.redirect_url = f"http://127.0.0.1:{other.server_port}/target"
        with HTTPClient(allow_loopback=True).open(
            self.url + "/redirect", {"Authorization": "Bearer synthetic"}
        ) as r:
            self.assertEqual(r.read(), b"synthetic")
        self.assertIsNone(self.redirect_auth)

    def test_drive_request_uses_real_http_standin_without_persisting_token(self):
        self.set_source(dict(drive_file_id="synthetic_drive_file_id"))
        owner = self

        class Mapped(HTTPClient):
            def open(self, url, headers=None):
                owner.assertEqual(
                    url,
                    "https://www.googleapis.com/drive/v3/files/synthetic_drive_file_id?alt=media",
                )
                return super().open(owner.url + "/payload", headers)

        client = Mapped(allow_loopback=True)
        with (
            patch("explorer_ingest.distribution_fetch.HTTPClient", return_value=client),
            patch.dict(os.environ, {"EXPLORER_DRIVE_TOKEN": "synthetic-private-token"}),
        ):
            self.fetch()
        self.assertEqual(self.requests[-1]["auth"], "Bearer synthetic-private-token")
        for p in (self.dest / ".download-state").iterdir():
            if p.is_file():
                self.assertNotIn(b"synthetic-private-token", p.read_bytes())

    def test_pinned_descriptor_and_existing_destination_cannot_be_rebound(self):
        self.fetch()
        old = self.manifest.read_text()
        value = json.loads(old)
        value["release_id"] = "different"
        self.manifest.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            self.fetch()

    def test_invalid_partial_length_rejected_without_another_request(self):
        self.mode = "disconnect"
        with self.assertRaises(Paused):
            self.fetch()
        checkpoint = self.dest / ".download-state/one.json"
        value = json.loads(checkpoint.read_text())
        value["bytes"] = -1
        checkpoint.write_text(json.dumps(value))
        calls = len(self.requests)
        with self.assertRaisesRegex(ValueError, "checkpoint length"):
            self.fetch()
        self.assertEqual(len(self.requests), calls)

    def test_empty_root_level_artifact_is_verified_without_http(self):
        value = json.loads(self.manifest.read_text())
        empty = hashlib.sha256(b"").hexdigest()
        value["artifacts"][0].update(
            path="empty.bin",
            bytes=0,
            content_bytes=0,
            sha256=empty,
            content_sha256=empty,
            encoding="raw",
        )
        self.manifest.write_text(json.dumps(value))
        self.sha = digest_file(self.manifest)
        self.set_source(dict(url=self.url + "/payload"))
        self.fetch()
        self.assertEqual((self.dest / "empty.bin").read_bytes(), b"")
        self.assertEqual(self.requests, [])


class PostmanReleaseTests(unittest.TestCase):
    def test_distribution_cli_reaches_existing_archive_http_fixture(self):
        base = os.environ["ARCHIVE_TEST_BASE_URL"]
        with urllib.request.urlopen(base + "/source/document.pdf") as response:
            payload = response.read()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            source = root / "source.pdf"
            source.write_bytes(payload)
            recipe = dict(
                release_id="standin-cli",
                visibility="public",
                inventory=dict(
                    snapshot_id="fixture", source_sha256="a" * 64, unique_assets=0, complete=False
                ),
                artifacts=[
                    dict(
                        id="pdf",
                        source=str(source),
                        group="viewer",
                        role="provenance",
                        path="files/fixture.pdf",
                        install_path="fixture/document.pdf",
                        encoding="raw",
                    )
                ],
            )
            path = root / "recipe.json"
            path.write_text(json.dumps(recipe))
            build_release(path, root / "package", reserve_bytes=0)
            manifest = root / "package/release.json"
            sha = digest_file(manifest)
            sources = root / "sources.json"
            sources.write_text(
                json.dumps(
                    dict(
                        format="explorer-release-sources",
                        version=1,
                        release_sha256=sha,
                        artifacts=dict(pdf=dict(url=base + "/source/document.pdf")),
                    )
                )
            )
            import sys

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/distribute_data.py",
                    "fetch",
                    "--manifest",
                    str(manifest),
                    "--expected-sha256",
                    sha,
                    "--sources",
                    str(sources),
                    "--destination",
                    str(root / "data"),
                    "--allow-loopback",
                    "--reserve-bytes",
                    "0",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((root / "data/files/fixture.pdf").read_bytes(), payload)
