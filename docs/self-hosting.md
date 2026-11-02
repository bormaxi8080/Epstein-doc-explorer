# Self-hosting a reviewed bundle

Build and validate the archive on a workstation. The public application needs
only the Explorer bundle and frontend; it never needs NVIDIA or FCC credentials.

The fork adds `TAG_CLUSTERS_PATH` beside the existing `DB_PATH`, allowing both
artifacts to point to one published bundle. The API opens an existing database
read-only by default; only `DB_READONLY=false` enables legacy writable/WAL mode.
The container serves the existing
frontend/API; the evidence tables are not yet exposed in the upstream UI.

## Operator procedure

1. Export a reviewed bundle as described in [ingestion.md](ingestion.md).
2. Copy it to `/srv/explorer/releases/<version>`. Check every SHA-256 against
   `manifest.json`. Keep the original upstream baseline separately.
3. Make `/srv/explorer/current` point to the selected release. Never mix its
   database with another release's cluster JSON. Restart the API after switching;
   an existing SQLite connection continues reading its previously opened file.
4. Set `EXPLORER_DATA_DIR=/srv/explorer` and `EXPLORER_DOMAIN=your.domain` in the
   deployment environment. Point DNS to the host, then run:

```bash
docker compose -f deploy/compose.yaml config
docker compose -f deploy/compose.yaml up --build -d
```

The server process runs as the image's `node` user with a read-only data mount.
Exported bundles use a standalone DELETE-journal database and need no writable
WAL/SHM sidecars. Grant read access to the bundle. Do not make the source
checkout or secrets publicly served. Caddy obtains HTTPS certificates when DNS
and ingress permit it. Back up data and certificate volumes independently.

Validate `/health`, `/api/stats`, `/api/tag-clusters`, filtered relationships and
a known document's text. Health alone does not check corpus completeness. Roll
back by selecting the previous complete bundle and restarting the service.

## Current limits

This is a deployment scaffold, not a live deployment. Original bytes are not
packaged or served automatically. Existing API query caps, date limits, proxy
rate-limit behavior and evidence/collection UI need review before a large public
launch. Images currently use major-version tags; pin reviewed image digests
before production release. Docker build/runtime and real-domain TLS require
verification on the target host.
