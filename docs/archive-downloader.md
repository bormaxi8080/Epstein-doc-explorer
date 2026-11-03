# Standalone PW-010 archive downloader

This script acquires original files from a finished PW-010 inventory. It is
independent of OCR, model calls, Explorer's database and the Oriol runtime.
Installation, `plan`, `status`, `verify` and `quarantine` do not make HTTP requests.
Only an explicit `fetch` does. No real archive was fetched during development.

## Independent locations

- `--dump`: the original, uncompressed PW-010 JSON file, placed wherever the
  operator chooses. This is an inventory, not an archive payload or latest pointer.
- `--evidence-db`: optional closed PW-010 SQLite backup in another location.
  A backup is opened read-only with immutable/query-only SQLite settings;
  WAL/SHM/journal sidecars are rejected. Do not pass the currently running crawler's
  database or copy a live WAL database without using SQLite backup facilities.
- `--destination`: an existing absolute directory on the selected data disk.
  It must be outside this checkout. The script never creates this root, mounts
  a disk or substitutes a system-disk location. `/Volumes/<disk>` must be mounted.
- `--config`: optional settings JSON, independent of all data paths. The tracked
  [example](../examples/archive-downloader.json) contains defaults, not credentials.

The JSON is authoritative for assets and structure. A DB is optional identity
evidence: its `meta.snapshot_id` must equal JSON `metadata.snapshot_id`. Its hash
and role are recorded; it is not used as a downloader ledger or silently queried
to add files missing from JSON. Matching UUIDs do not prove that a DB and JSON are
the exact publication pair; preserve the producer's generation manifest for that
additional evidence. DB-only input and compressed backups are not supported.

## Commands after the final dump is available

Use the project's existing Python environment (`pip install -e .` for a new
checkout). All input paths below are examples; none is a configured live source.
Mount the external disk and explicitly create the chosen destination beforehand.
Run from the fork root:

```bash
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/ArchiveDisk/EA-download \
  --config examples/archive-downloader.json \
  plan --dump /path/to/inventory/doj_disclosures.json \
  --evidence-db /another/path/to/closed-checkpoint.sqlite3
```

Omit `--evidence-db` to use JSON alone. `plan` performs no download. It stores an
immutable SHA-256-named copy of JSON, validates the pinned v1/v2 schema fragments,
root keys, hierarchy, all asset/page/occurrence references, stable page evidence
and registry/placement/byte/issue counts. It builds indexes in SQLite with several
streaming passes rather than materializing the complete JSON tree in memory.
The original JSON preserves every field, external link and page reference.

By default, all producer completeness flags must be true, there must be no issues,
every asset must have a verified size, and observed/verified page hashes must match.
A truncated, inconsistent or invalid JSON file never creates a ready plan. An
unfinished inventory can only be used with explicit `plan --allow-partial` and
`fetch --allow-partial`; such a pilot never becomes a certified complete corpus.
Keep waiting for the final dump for the actual full acquisition.

```bash
# Offline ledger summary; not a fresh audit of disk contents.
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/ArchiveDisk/EA-download status

# Explicit network command; a bounded first batch when ready.
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/ArchiveDisk/EA-download \
  --config examples/archive-downloader.json fetch --limit 100

# Continue all pending files with the same immutable plan.
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/ArchiveDisk/EA-download \
  --config examples/archive-downloader.json fetch

# Offline full audit; required before downstream processing.
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/ArchiveDisk/EA-download \
  --config examples/archive-downloader.json verify
```

`--limit` on `verify` is a bounded diagnostic and cannot certify the whole corpus.
A destination belongs to one JSON SHA-256. Repeating `plan` with identical JSON is
idempotent; a different dump requires another destination. No implicit refresh
replaces existing originals. After package reinstallation, `explorer-download`
is an equivalent console entry point; the separate script works directly.

## Structure on the external disk

```text
EA-download/
├── archive-files/<host>/<URL directories>/<original-name>--<identity>.pdf
├── catalog/<DOJ section>/<nested group>/<document>.ref.json
└── .pw010-download/
    ├── identity.json
    ├── archive.sqlite3
    ├── writer.lock
    ├── inputs/<JSON SHA-256>.json
    ├── partials/<asset identity>.part
    ├── partials/<asset identity>.json
    ├── receipts/<asset identity>.json
    ├── quarantine/<case identity>/
    ├── progress.json
    ├── report.json
    └── verified-files.jsonl
```

Physical URL directories and logical DOJ hierarchy are preserved separately.
Unsafe characters are escaped; components carry stable suffixes to distinguish
case collisions, duplicate names, different URLs and query-bearing resources.
The exact original names, IDs, URLs, hierarchy and labels remain in input JSON
and metadata. One asset is downloaded once; every observed placement gets its
own reference to that file. Different URLs remain distinct original assets even
when their bytes happen to match. No hardlinks or symlinks are required, and ZIP
contents are not extracted. This permits later reconstruction of container,
member, document and page provenance without altering the original ZIP.

SQLite is this script's resumable ledger, completely separate from the optional
input DB and the future analysis DB. A writer lease prevents concurrent owners.
Do not delete its lock/identity files to bypass an active process. The selected
root/device/inode and ledger identity are rechecked during operations; descendants
are created without recursively recreating a missing destination. OS/filesystem
behavior during forced device removal still governs physical durability.

## Transfer and resume contract

- Only configured hosts are allowed, including every redirect. HTTPS validation
  is enabled, HTTPS downgrades are rejected, and plain HTTP requires explicit
  configuration. No browser cookies, proxy credentials or age-gate bypass is used.
- Request identity encoding so received bytes can be compared with advertised
  sizes. Enforce per-file limits, socket timeout and remaining disk reserve.
- A durable partial stores its own checked byte-prefix hash and strong ETag.
  Verify that prefix before `Range`/`If-Range`. Weak/missing ETags, corrupt partials
  or responses returning 200 cause a restart, never concatenation of versions.
  Invalid 206 ranges/ETags/lengths fail closed. 416 permits a bounded full restart.
- Interrupted transfers retain durable progress. Retry transport failures and
  selected 408/429/5xx responses at most the configured count; numeric Retry-After
  is honored with a 30-second cap. There is one sequential worker and a configured
  pause between assets, avoiding an uncontrolled parallel download burst.
- Check expected size and optional source SHA-256, reject HTML masquerading as
  binary data, validate content, then record a receipt and publish the original.
  Receipts precede rename so interrupted publication can be reconciled without
  another GET. Successful originals are never silently overwritten.
- Disk reserve/ENOSPC or changed storage identity pauses the command; it does not
  allocate emergency data on the system disk. Space checks are admission/monitoring
  guards, not filesystem quotas. Reconnect/free storage and invoke the script again.

The config defaults bound individual files to 64 GiB, ZIP validation to 20 GiB
expanded data/one million members, reserve 1 GiB free, and use two retries. Review
the actual catalog sizes before increasing limits. A valid but larger ZIP needs
an explicit higher validation budget. Planning reserves an estimate of three
times JSON size plus the free-space floor; real ledger growth remains dependent
on the dump. All effective fetch settings are retained in the ledger.

## Integrity, completeness and correctness

These are distinct claims:

| Claim | Evidence |
| --- | --- |
| Source inventory complete | PW-010 flags plus consumer registry/reference/count checks |
| Transfer complete | Every unique asset has its expected bytes and a download receipt |
| Local integrity | Offline rehash equals the receipt; optional trusted source hash also matches |
| Structural correctness | Strict PDF structure/end marker; streaming ZIP CRC and member safety/budgets; UTF-8 text decoding |
| Limited format checks | Media signatures, encrypted PDF headers, opaque files and DAT/OPT preservation are explicitly marked as not fully checked |

`verify` also checks SQLite integrity, the saved JSON checksum and every logical
reference. A successful full audit publishes `verified-files.jsonl` with its own
SHA-256 in `last_full_audit`. Any new fetch/quarantine or failed/bounded verification
invalidates that manifest. Reports distinguish `download_complete`, producer
certification and `complete_at_last_audit`; the last is historical evidence at its
recorded timestamp. Run a fresh full audit after moving/restoring/editing data.

PW-010 normally has no authoritative binary hashes. A newly computed SHA-256
protects later local integrity; it does not independently prove authenticity or
detect a same-size, structurally valid server replacement between inventory and
download. Receipts retain final URL, response ETag, MIME, time and measured hash.
Full media decoding, OCR accuracy and semantic analysis remain later stages.

Errors preserve the last durable data. To retry a suspect managed artifact after
review, explicitly move it and its receipts/partials into quarantine:

```bash
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/ArchiveDisk/EA-download quarantine ASSET_ID
```

Then `fetch` can retrieve only that reset/pending asset. The previous suspect
bytes and provenance stay in quarantine. Missing derived references from an
interrupted publication can be reconciled locally. No script cleans arbitrary
files, purges quarantine or changes the source DB.

Exit codes: 0 means the requested operation succeeded; 1 reports invalid inputs
or failed assets; 2 means paused storage, a bounded/incomplete run, or an audit
that cannot certify the whole source inventory. Inspect `report.json`, per-asset
ledger errors, receipts and `progress.json` rather than using process exit alone.

## Boundary with later OCR and Explorer ingestion

After a fresh successful audit, the next-stage adapter can read
`.pw010-download/verified-files.jsonl` and its referenced receipts, resolve
`relative_path` under this root, and verify the listed file hash. Each JSONL row
includes original asset ID, bytes, SHA-256, complete source metadata/occurrences
and receipt path. Logical catalog references and immutable JSON recover the tree.
Archive members will receive separate IDs linking back to the unchanged container
when the later extraction/OCR stage is built. OCR outputs, page coordinates,
models, extracted relationships and Explorer exports belong in separate derived
storage. This downloader does not launch or certify those future stages, and the
older pilot `explorer-ingest download` is not the full archive mirror.

## Offline/local verification

```bash
PYTHONPATH=ingestion .venv/bin/python -m unittest discover -s ingestion/tests -p test_archive.py -v
postman simulation run postman/simulations/archive.sim.yaml --port 4011 --no-history --output ndjson
# In another terminal:
ARCHIVE_TEST_BASE_URL=http://127.0.0.1:4011/archive \
  PYTHONPATH=ingestion .venv/bin/python -m unittest discover -s ingestion/tests -p test_archive.py -v
```

Tests use only temporary synthetic PDFs/ZIPs/SQLite/JSON and loopback sources.
They exercise separate input paths, source validation, read-only evidence,
Range/If-Range, disconnects, 200 fallback, bad ranges, corrupt partials, 429/500,
HTML rejection, CRC, quotas, corruption/quarantine, publication recovery, logical
references and device/root disappearance. The Postman fixture models a static
source subset; no provider-owner mock or real DOJ endpoint was consulted.
Actual final dump scale, source-specific gates/redirects/limits, external disk
behavior and supported source formats must be checked in a later supervised pilot.

## Verification record (2026-11-03)

- 45 tests passed: 28 downloader checks and 17 existing ingestion checks, using
  only temporary fixtures and local archive/FCC stand-ins. No tests were skipped.
- The separate script was invoked as a subprocess for offline plan, explicit
  loopback fetch and offline verify. The Postman request log confirmed its GET.
- Ruff checks/formatting, API typechecking and local documentation links passed.
- No real dump, source DB, archive download, external data disk, OCR job or model
  invocation was used. A supervised pilot remains necessary after the final
  producer snapshot is available.

The bundled schema resources are pinned copies of the producer's
`oriol.tasks/node-1/pw-010/doj_inventory/dump.schema.json` and
`dump.compact.schema.json`. There is no runtime import of Oriol. Upgrade these
resources and fixtures explicitly if the producer contract changes.

| Resource | SHA-256 |
| --- | --- |
| `pw010-v1.schema.json` | `bb9b71b40f1539cf43d2535252888db74b088cbb467c0912dd0407720286a7be` |
| `pw010-v2.schema.json` | `d29961fed7e4172835355f4c59afbd7bb6a65f4bf3620ca5555ea8500ade506e` |
