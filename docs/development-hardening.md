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
compatible. Four focused handoff tests passed using synthetic local HTTP data.
