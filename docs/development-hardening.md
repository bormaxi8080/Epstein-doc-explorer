# Archive hardening and offline development

## Delivered: artifact contracts

`archive-artifacts-v1.schema.json` defines identity, receipt, partial, reference,
progress, audit, report and verified JSONL row contracts. Producers validate before
publication and readers reject incompatible versions and unsafe relative paths.
Legacy v1 rows without an explicit version remain supported; additive optional
fields are allowed. Golden synthetic v1 fixtures protect compatibility.
Reports also check status totals and completeness claims against audit evidence.
Quarantine CLI output stays an operation result, while `report.json` remains a
consistent corpus report.

Verification: existing 48-test suite passed before the final semantic contract
checks; all four focused contract tests then passed. No real source was contacted.

Further hardening stages are implemented in separate commits and documented here
as each stage becomes available. The archive acquisition guide remains the
operator entry point.

## Delivered: bounded validators and crash recovery

PDF/ZIP/text/media checks execute in a separate Python process with a default
30-second wall/CPU budget and 512 MiB resident-memory cap. Linux also enforces an
address-space limit; macOS monitors child RSS. A worker has a parent-death watcher,
wall alarm, no bytecode writes and no permission to create regular-file output.
The parent terminates and reaps the worker on timeout, memory excess, storage
failure or operator interruption. Limits are configurable in downloader settings.
These are process/resource controls, not an OS network/filesystem sandbox.

SIGTERM/Ctrl-C stop the CLI with a resumable pause. Abrupt exit tests cover six
durability boundaries, including partial checkpoint, receipt, original rename,
references, ledger commit and audit publication. An unreceipted complete partial
restarts safely rather than requesting a range beyond EOF. Originals are never
silently overwritten. Five focused tests passed, including actual child memory
allocation and forced subprocess exits; prior unrelated regression checks passed.

## Delivered: synthetic scale and document corpora

`python scripts/benchmark_archive.py --count 100000 --output /tmp/scale.json`
generates and plans a temporary synthetic inventory, records peak RSS, timing,
SQLite/scratch sizes and planner hash, and removes all large scratch files.
`--count 1000000` is an explicit million-file run; it is not part of fast tests.
Admission and periodic checks preserve an 8 GiB scratch reserve by default, and
an RSS acceptance threshold defaults to 512 MiB. No fixture URL is downloaded.

The planner caches validators and at most 8192 directory paths. Parent/status
indexes avoid repeated scans while preserving the same portable output paths.
A 10,000-asset run used about 39 MB RSS and 1.7 s planning on the development
machine; larger measured runs are recorded separately, not inferred as an SLA.

`python scripts/generate_corpus.py /tmp/synthetic-corpus` creates labeled text,
rotated, table, image-only, encrypted and corrupt PDFs, a blank PNG, a two-page
TIFF and safe/unsafe ZIPs. Everything is synthetic. These fixtures verify routing
and robustness, not real OCR accuracy; encryption bytes may vary with toolchain.
Two targeted generation/planning/extraction tests passed.

## Delivered: audited archive adoption

`explorer-ingest --data-root /path/to/derived adopt-archive /Volumes/Archive/doj`
requires a certified inventory, successful full audit and unchanged verified
manifest. It streams every manifest row through schema, ledger and receipt
checks and rehashes every original before linking it. This is deliberately a
full disk read; it does not call a model, download or start extraction.

The independent ingestion ledger retains the input JSON and every occurrence;
original payloads stay on the archive disk. Asset/document provenance uses
`archive://<uuid>/<relative-path>` references. Repeating adoption is idempotent;
explicit re-adoption can update a moved archive's location when UUID, inventory
and manifest match. Partial/failed audits and conflicting existing bytes fail.
Extraction leases each source archive for the batch, checks its audit binding
and hashes each original again; derived pages and ZIP members stay in data-root.
Archive and derived roots must not overlap. Existing pilot download remains
compatible. Six focused handoff tests passed using synthetic local HTTP data, including
changed manifests and receipts rebound to another source snapshot.

## Delivered: observation, preflight and metadata recovery

Only `fetch` uses source HTTP. These commands accept the same independent
`--destination` and optional `--config` as the downloader:

```bash
python scripts/download_archive.py --destination /Volumes/Archive/doj doctor
python scripts/download_archive.py --destination /Volumes/Archive/doj doctor --write-probe
python scripts/download_archive.py --destination /Volumes/Archive/doj status --watch --interval 2
python scripts/download_archive.py --destination /Volumes/Archive/doj backup /path/to/new-backup
python scripts/download_archive.py --destination /Volumes/Replacement/doj restore /path/to/new-backup
python scripts/download_archive.py --destination /Volumes/Replacement/doj verify
```

`status` now opens SQLite read-only, even while a writer owns the archive. Watch
emits JSONL snapshots (`--samples N` bounds a run), status/error counts, writer
presence, sampled byte rate and ETA. The rate includes checkpointed bytes for
the active transfer and is approximate; the checkpoint interval is five seconds.
ETA is null without a positive rate or when any sizes are unknown. A status
snapshot is never a fresh disk audit and does not rewrite `report.json`.
`doctor` checks the existing filesystem, reserve, dependencies, TLS CA setup and
ledger integrity without network traffic. An optional write probe creates and
removes its own small directory to test fsync, rename, case and Unicode behavior.
Neither command creates a missing destination or mounts a disk.

`backup` holds the archive writer lease, uses SQLite backup and saves the frozen
inventory, identity and canonical receipts with a versioned checksummed JSONL
file list. It excludes originals, partial bytes, credentials and derived OCR.
Keep a separate backup of original payloads; a metadata backup cannot recreate
missing original bytes without another explicit `fetch`. Output must be a new
external directory. The configured free-space reserve and destination filesystem
identity are checked while copying. Copied files and nested directories are fsynced before
publication; loss/replacement of the metadata disk stops the operation.

`restore` verifies every backup member before publishing metadata and refuses
any existing control directory. It preserves archive UUID, resets statuses and
invalidates the old audit. Existing original bytes are untouched; references
are regenerated by `verify`. Missing-original receipts are retained under
`historical-receipts` so an explicit `fetch` can retry. Corrupt originals must be
preserved/quarantined rather than overwritten. Run a full `verify` and re-adopt
before extraction; restored metadata never implies completeness.

If the ledger alone is lost, preserve a copy of all remaining metadata first.
An explicit missing-ledger recovery is available:

```bash
python scripts/download_archive.py --destination /Volumes/Archive/doj rebuild \
  --dump /path/to/frozen-inventory.json --receipts /path/to/saved-receipts
```

It refuses an existing ledger, re-plans the authoritative JSON, checks receipt
source identities and original SHA-256, then performs a full offline audit.
Only receipted, matching originals are recoverable. The existing identity marker
is retained; a missing marker creates a new UUID, requiring a new derived root.
Rebuild never guesses hashes or trusts matching sizes. On a failure, provisional
metadata and original bytes remain for inspection; use `verify`, quarantine and
explicit fetch to proceed rather than replacing the ledger again. Seven focused
operation tests passed against temporary synthetic archives, including
destination identity loss.

## Measured scale

A synthetic 1,000,000-asset / 1,000-page inventory planned in **186.21 seconds**,
with **38,830,080 bytes peak RSS**, 1.64 GB SQLite and 2.70 GB total scratch.
Generation took 7.55 seconds. Python 3.12.10 on macOS; no HTTP/model calls.
The [machine-readable record](benchmarks/million-assets-2026-11-03.json) includes
the measured planner SHA-256. This measures JSON planning only, not downloading,
content validation, adoption, OCR throughput or real archive shape. Earlier
100,000-asset exploratory planning took 16.71 seconds / 40.17 MB RSS. Timing is
machine-dependent and is not a service guarantee. Large scratch files were
removed after each run.

## Delivered: repeatable offline checks and CI

```bash
python scripts/check_offline.py
python scripts/check_offline.py --pattern test_archive_operations.py
```

The runner launches the existing Node archive and FCC handlers with loopback
binding and temporary logs, sets both test URLs, checks that external DNS and
connect attempts are rejected by an inherited Python audit hook, and cleans up
its own server processes. This is a test guard, not an OS sandbox. No Postman
account or installed CLI is required; the same handlers remain usable through
Postman simulation for detailed traffic inspection.

`.github/workflows/offline-ingestion.yml` runs lint/format, all synthetic tests,
10k-asset planning and corpus generation on Python 3.10/3.12, plus the scoped API
typecheck. Checkout excludes real archive/LFS data, tests use no provider secrets,
and nothing deploys or downloads archive payloads. Dependency installation uses
package registries. Manual workflow dispatch selects 10k, 100k or million-asset
planning and uploads only its synthetic measurement JSON. Large tests do not run
on ordinary pushes. Development tools are available with `pip install -e '.[dev]'`.

Final local checks on Python 3.12.10 passed: **69 tests, no skips**, Ruff lint and
format, API typecheck, workflow YAML parsing, 10k-asset planner smoke, synthetic
corpus generation and Python wheel inclusion of all four JSON schemas.
The GitHub-hosted workflow and its Linux/Python 3.10 matrix have not yet run.
Live FCC/NVIDIA compatibility, real-source transfers, actual corpus completeness,
OCR accuracy and production hosting still require the later authorized pilot.
