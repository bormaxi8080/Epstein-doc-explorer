"""Create offline, checksum-bound Google Drive/mirror maps after an operator upload."""

import json
from pathlib import Path

from .distribution_contracts import ExternalDirectory, load_release
from .distribution_fetch import HTTPClient, locator
from .state import atomic_json, digest_file


def write_sources(manifest, output, mapping=None, allow_hosts=()):
    manifest, output = Path(manifest), Path(output).absolute()
    release = load_release(manifest)
    storage = ExternalDirectory(output.parent, 0)
    if output.exists() or output.is_symlink():
        raise ValueError("Source map output must be new")
    sha = digest_file(manifest)
    ids = {a["id"] for a in release["artifacts"]}
    if mapping:
        path = Path(mapping)
        if path.stat().st_size > 8 * 1024**2:
            raise ValueError("Source mapping exceeds its budget")
        values = json.loads(path.read_text())
        if values.get("release_sha256") != sha or set(values.get("artifacts", {})) != ids:
            raise ValueError("Uploaded mapping must bind every artifact and the pinned release")
        client = HTTPClient(allow_hosts=allow_hosts)
        for value in values["artifacts"].values():
            url, _ = locator(value)
            client.validate(url)
        result = dict(
            format="explorer-release-sources",
            version=1,
            release_sha256=sha,
            artifacts=values["artifacts"],
        )
    else:
        result = dict(
            format="explorer-source-map-input",
            version=1,
            release_sha256=sha,
            artifacts={a["id"]: None for a in release["artifacts"]},
            upload_paths={a["id"]: a["path"] for a in release["artifacts"]},
        )
    storage.check()
    atomic_json(output, result)
    return dict(
        output=str(output),
        ready_for_fetch=bool(mapping),
        artifacts=len(ids),
        release_sha256=sha,
        network_calls=False,
    )
