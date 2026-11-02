"""Bounded, verified downloads. Payloads never enter the source repository."""

import json
import os
import urllib.request
from urllib.parse import urlparse

from .state import digest_file
from .transport import tls_context


def download_assets(store, limit=10, max_bytes=1024**3, timeout=60, allow_hosts=None):
    rows = store.db.execute(
        "SELECT * FROM assets WHERE status != 'downloaded' ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    results = []
    directory = store.root / "originals"
    directory.mkdir(exist_ok=True)
    for row in rows:
        partial = directory / (row["id"] + ".part")
        try:
            metadata = json.loads(row["metadata"])
            url = metadata.get("download_url") or metadata["url"]
            allowed = set(allow_hosts or ["www.justice.gov", "justice.gov"])

            def check_url(value, allowed_hosts=frozenset(allowed)):
                parsed = urlparse(value)
                if (
                    parsed.scheme not in ("http", "https")
                    or parsed.hostname not in allowed_hosts
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError("Download host is not explicitly allowed")

            check_url(url)

            class Redirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, request, fp, code, msg, headers, newurl):
                    check_url(newurl)
                    return super().redirect_request(request, fp, code, msg, headers, newurl)

            expected = metadata.get("size_bytes")
            if expected is not None and expected > max_bytes:
                raise ValueError("Advertised size exceeds --max-bytes")
            # Restart an interrupted transfer; never concatenate unverified ranges.
            opener = urllib.request.build_opener(
                Redirect(), urllib.request.HTTPSHandler(context=tls_context())
            )
            with (
                opener.open(
                    urllib.request.Request(
                        url, headers={"User-Agent": "EpsteinExplorerIngest/1.1"}
                    ),
                    timeout=timeout,
                ) as response,
                partial.open("wb") as output,
            ):
                received = 0
                while block := response.read(1024 * 1024):
                    received += len(block)
                    if received > max_bytes:
                        raise ValueError("Transfer exceeds --max-bytes")
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if expected is not None and received != expected:
                raise ValueError("Downloaded size differs from inventory")
            sha = digest_file(partial)
            destination = directory / sha
            partial.replace(destination)
            with store.db:
                store.db.execute(
                    "UPDATE assets SET status='downloaded',sha256=?,path=?,error=NULL WHERE id=?",
                    (sha, store.relative(destination), row["id"]),
                )
            results.append({"asset_id": row["id"], "status": "downloaded", "bytes": received})
        except Exception as exc:
            partial.unlink(missing_ok=True)
            # Do not persist response bodies, headers or credential-bearing URLs.
            with store.db:
                store.db.execute(
                    "UPDATE assets SET status='download_failed',error=? WHERE id=?",
                    (type(exc).__name__, row["id"]),
                )
            results.append(
                {
                    "asset_id": row["id"],
                    "status": "download_failed",
                    "error": str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                }
            )
    return results
