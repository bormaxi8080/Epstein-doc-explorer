# Standalone PW-010 archive downloader

This script acquires original files from a finished PW-010 inventory. It is
independent of OCR, model calls, Explorer's database and the Oriol runtime.
Installation, `plan`, `status`, `verify` and `quarantine` do not make HTTP requests.
Only an explicit `fetch` does. No real archive was fetched during development.

## Independent locations

- `--dump`: the original, uncompressed PW-010 JSON file, placed wherever the
  operator chooses. This is an inventory, not an archive payload or latest pointer.
- `--manifest`: alternative to `--dump` for `plan`/`rebuild`; selects one complete,
  checksummed partitioned inventory. Keep it with every listed part.
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
A destination belongs to one original JSON SHA-256. Partitioned representations
retain this identity and have separate manifest/member hashes. Repeating `plan` with identical JSON is
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
  configuration. Session cookies use the optional operator-supplied cookie file.
- An optional absolute `cookie_file` setting loads a Netscape cookie jar obtained
  through normal browser access/age confirmation. Cookie values stay outside Git,
  reports and event logs. The downloader uses ordinary HTTP with that session.
- Request identity encoding so received bytes can be compared with advertised
  sizes. Enforce per-file limits, socket timeout and remaining disk reserve.
- A durable partial stores its own checked byte-prefix hash and strong ETag.
  Verify that prefix before `Range`/`If-Range`. Weak/missing ETags, corrupt partials
  or responses returning 200 cause a restart, never concatenation of versions.
  Invalid 206 ranges/ETags/lengths fail closed. 416 permits a bounded full restart.
- Interrupted transfers retain durable progress. Retry transport failures and
  selected 408/429/5xx responses at most the configured count; numeric and HTTP-date Retry-After
  are honored with a five-minute cap. There is one sequential worker and a configured
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
an explicit higher validation budget. Planning reserves an estimate of five
times original JSON size plus the free-space floor; real ledger growth remains dependent
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

`verify` also checks SQLite integrity, the saved JSON checksum (or all manifest
members and the semantic signature) and every logical reference. A source-check
failure invalidates the prior successful audit. A successful full audit publishes `verified-files.jsonl` with its own
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

## Preparation, observation and recovery

See [offline development and hardening](development-hardening.md) for versioned
artifact contracts, bounded validators, crash tests, synthetic corpora/scale,
`adopt-archive`, read-only `status --watch`, `doctor`, metadata backup/restore and
missing-ledger recovery. Metadata backups exclude original payloads; restoring
always requires a fresh full audit before adoption.


## Fixed first-in-tree pilots and logical originals

`plan --dump INPUT --layout logical` retains the complete authoritative input and
validates its full registries while assigning original files to `originals/`
under their first logical tree placement. Directory names retain readable labels
with stable identity suffixes, unsafe characters are replaced, and filename
collisions get asset suffixes. Every source placement retains its catalog
reference. The existing default `--layout url` keeps URL-based originals.
An existing plan retains its chosen layout.

After planning, `pilot --first 250` freezes the first 250 unique assets in tree
order. `.pw010-download/pilot.json` records ranks, IDs, paths, sizes and the source
SHA-256. Repeating the same selection is idempotent. Membership stays fixed across
failures and restarts. Fetch with `--pilot` to operate only on that membership.

```bash
.venv/bin/python scripts/download_archive.py --destination "$ARCHIVE_DESTINATION" \
  --config "$ARCHIVE_CONFIG" plan --dump "$ARCHIVE_DUMP" --layout logical
.venv/bin/python scripts/download_archive.py --destination "$ARCHIVE_DESTINATION" pilot --first 250
.venv/bin/python scripts/download_archive.py --destination "$ARCHIVE_DESTINATION" \
  --config "$ARCHIVE_CONFIG" fetch --pilot --limit 10
.venv/bin/python scripts/download_archive.py --destination "$ARCHIVE_DESTINATION" \
  --config "$ARCHIVE_CONFIG" fetch --pilot
.venv/bin/python scripts/download_archive.py --destination "$ARCHIVE_DESTINATION" \
  --config "$ARCHIVE_CONFIG" verify --pilot
```

`fetch --pilot` returns 0 when every selected asset is downloaded. `verify --pilot`
returns 0 after a fresh successful audit of all selected assets and publishes
`pilot-verified-files.jsonl`. Full-inventory totals and certification remain
explicit in the report, alongside the separate `pilot` counters/audit. A bounded
`verify --pilot --limit N` cannot pass its audit. Further fetch/quarantine clears
the pilot audit and manifest. Full-corpus adoption still requires a full audit.

New ledgers use 64 KiB SQLite pages for external-disk writes. The planner uses
a bounded 256 MiB SQLite page cache and builds its asset-to-node index after tree
import. Existing ledger page sizes are preserved. `events.jsonl` records planning stages, file attempts,
completion, retry errors and verification progress, also emitted to stderr.
Writer CLI commands set `TMPDIR` and `SQLITE_TMPDIR` to `.pw010-download/tmp`
under the selected destination for disk-backed scratch. Ordinary originals, inputs, ledger, partials and receipts use that root.
Validators retain their separate configured memory/time bounds. Transient native
memory-monitor failures retry at most three times before a classified validation
error. Strong response ETags are compared with strong inventory ETags when both
are available, and the comparison is recorded in receipts.

Rebuilding a lost logical-layout ledger uses `rebuild --layout logical` with the
same source and receipts. Metadata restore invalidates prior audit results;
perform a fresh `verify --pilot` for a restored pilot before using its files.

Pilot summaries force membership-first joins so a small selection performs
bounded indexed lookups instead of scanning the full registry. CLI progress
callbacks make long SQLite statements interruptible by SIGINT/SIGTERM. Temporary
directory environment settings and signal handlers are restored on return to
embedded callers. Repeating `pilot --first N` regenerates a missing derived
selection manifest from the durable ledger.

`verify --pilot` checks the selected membership table and its foreign keys,
the complete pinned JSON SHA-256, and every selected file/receipt/placement.
The audit records `scope=pilot` and its ledger integrity scope. SQLite 3.33+
supports the table-level integrity check; earlier SQLite falls back to the
whole-ledger check. Full-corpus `verify` checks the complete ledger. The separate
pilot result covers its frozen membership. SQLite documents table-level checks
in [PRAGMA integrity_check](https://www.sqlite.org/pragma.html#pragma_integrity_check).


## Authorized live pilot (2026-11-11)

The operator authorized the first 250 files from the complete PW-010 1.0.42
inventory at `/Volumes/Transcend/data/EA/doj_disclosures.json`. The independent
output root is `/Volumes/Transcend/data/EA-download`. All copied inputs, SQLite,
originals, partials, receipts, event logs and scratch reside on that external disk.

- Source snapshot: `292e4f9c7dfd447097cf8522e405dae8`.
- Source JSON SHA-256: `7e4791264d49dd256c1eaaf381e5da0764d2bc4d39f7fea56614abce740a47b8`.
- Full offline plan: 1,396,719 unique assets, 1,396,720 placements and 27,980 pages.
- Frozen pilot: `EFTA00000001.pdf` through `EFTA00000250.pdf` in Data Set 1.
- Downloaded and independently rehashed: 250 files, exactly 110,614,300 bytes.
- All 250 strong response ETags matched the inventory. All 250 PDF structure
  checks passed. Transfer error events: zero.
- A controlled SIGTERM after 25 completions returned a resumable pause. Those
  first 25 assets retained one transfer attempt each after continuation.
- Fresh `verify --pilot` completed at 2026-11-11 21:04:43 Asia/Almaty with
  `passed=true`, `checked=250`, `failures=0`, `all_content_fully_checked=true`,
  `scope=pilot` and `ledger_integrity_scope=pilot_assets`.
- Verified JSONL SHA-256: `687aeeebbe50700ead14662a2e4cdb7486e535698eb65757e92a822ea0714262`.

DOJ initially redirected the ordinary client to its age-confirmation page. A
normal Chrome confirmation produced an operator-authorized session, exported as
a private Netscape cookie file outside Git. The HTTP downloader then acquired
the originals. Cookie values are excluded from this record and event logs.

The live run exposed external-disk costs of small SQLite pages, an unbounded
join order for pilot summaries and whole-registry preflight in a scoped audit.
The delivered page/cache/index settings, membership-first joins and explicit
pilot integrity scope address those paths. Full archive verification retains
whole-ledger integrity checks. Directory labels carry stable suffixes and each
PDF retains its original filename. The external root holds about 6.69 GB including
the 1.65 GB pinned JSON, 4.93 GB ledger and 110.61 MB of original PDFs.

Final offline verification: 86 synthetic tests passed with external sockets
blocked. Ruff lint, formatting and Git whitespace checks passed. Local fixtures
cover HTTP failures, range resume, session transmission, scope/order, ledger
recovery, operator stop, native monitor recovery and audit invalidation. The
real interruption test establishes fixed-selection continuation. Real-source
nonempty Range/If-Range response behavior remains outside this pilot evidence.

Operator results are `verify-result.json`, `.pw010-download/report.json`,
`.pw010-download/pilot.json`, `.pw010-download/pilot-verified-files.jsonl` and
`.pw010-download/events.jsonl` under the selected root. The input path is an
independent `--dump` argument and output is an independent `--destination`.


## Partitioned inventory input

`split` is an explicit offline operation. It keeps the original dump unchanged,
creates a new immutable directory beside it, and never starts acquisition:

```bash
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/Transcend/data/EA-download \
  split --dump /Volumes/Transcend/data/EA/doj_disclosures.json \
  --output /Volumes/Transcend/data/EA/inventory-parts/292e4f9c7dfd447097cf8522e405dae8 \
  --target-mb 75
```

The output parent must already exist on the mounted external disk; the output
directory itself must not exist. `--target-mb` uses decimal, uncompressed MB and
accepts 1–100 (default 75). Records are never cut. A record larger than the target
gets its own part, up to the hard 100 MB limit; larger records fail explicitly.
The final part may be smaller. Manifest and parts are UTF-8 JSON. Each part has
one record per line within a `records` array and is independently valid JSON.
It is an inventory fragment, not a complete PW-010 dump or a downloadable corpus.

The version-1 `pw010-inventory-parts` manifest contains the original SHA-256 and
byte size, the complete producer metadata/validation, target size, ordered part
names, exact byte sizes, SHA-256 hashes and per-type record counts. A semantic
signature covers the complete header and ordered records of each type. Empty
issues are represented by a zero issue-record count; nonempty issue objects are
preserved as records, although splitting requires a certified complete source.

Records preserve these fields:

- `tree` / legacy `storage_tree`: preorder `seq`, parent sequence and the full
  original `data`, with `children` retained as an empty-array marker. Records are
  emitted in postorder. Reattach child records to their parents in ascending
  `seq` to reconstruct every directory, sibling order and original node field.
- `asset`: original asset-registry `id` and the complete original `data`, including
  every occurrence, URL, size and extra field.
- `page` / `issue`: complete original `data`.

Only child arrays are flattened. Shared assets remain one asset with multiple
placements. No absolute source directory, binary payload or credential is copied
into records. The schema remains separately identified as PW-010 v1/v2; partition
format version 1 does not change that producer contract.

The writer streams one node/asset/page at a time, checks the producer counters
and record schemas, hashes the original before and after reading, then reads
back every part to compare the complete semantic signature. All staging is in
the output's parent directory on the selected disk. Publication happens only
after verification; root/volume checks prevent recreating a missing disk path.
Progress is JSONL on stderr. An interrupted split has no published output and
can be repeated after its temporary-directory cleanup.

Connect the complete set using the same existing download destination:

```bash
.venv/bin/python scripts/download_archive.py \
  --destination /Volumes/Transcend/data/EA-download \
  plan --manifest /Volumes/Transcend/data/EA/inventory-parts/292e4f9c7dfd447097cf8522e405dae8/manifest.json
```

A new destination validates/pins all parts and runs the same schema, hierarchy,
page evidence, occurrence, count and path checks as `--dump`. Input files are
copied to `.pw010-download/inputs/bundles/<manifest SHA-256>/`; completed matching
parts are reused after interruption and the local manifest is copied last.
SQLite imports checkpoint every 5,000 records and at phase boundaries. An
unfinished plan cannot fetch or audit. Restart with the same manifest and layout
to resume committed records. Input integrity is checked again on restart; already
committed records in complete parts can be skipped during SQL import. Only the
final validated transaction makes the plan ready.

For an existing completed plan, attaching requires independently checksumming
and semantically comparing its saved original inventory with the complete input
set. Claiming the same origin SHA in a manifest is insufficient. Equivalent
attachment keeps the ledger's `source_sha256`, original paths, all download
statuses, receipts, attempt counts and frozen pilot order. It retains the saved
large JSON and invalidates old audits so a fresh `verify` is required. Reattaching
an already attached, unchanged valid set is idempotent and retains its audit.

`fetch` continues from the ledger without reading parts for each document.
`verify` checks every inventory part even for a pilot, then audits the requested
payload scope. `backup`, `restore`, `rebuild --manifest` and audited downstream
adoption preserve the whole set and support bundle-only archives. Metadata
backups containing bundles use format version 2; legacy single-JSON backups stay
at version 1. The source dump and payload directories are never moved or deleted.

Splitting does not reduce SQLite size or speed up HTTP requests. The normalized
set adds some record overhead. Retaining both the exported set and the pinned
copy adds roughly two set sizes on the external disk; old JSON copies remain.
Reserve checks protect free space but are not a filesystem quota.


### Verified local conversion, 2026-11-16

The completed snapshot `292e4f9c7dfd447097cf8522e405dae8` was converted without
modifying or moving `/Volumes/Transcend/data/EA/doj_disclosures.json` or its pinned
copy. Original SHA-256 remains
`7e4791264d49dd256c1eaaf381e5da0764d2bc4d39f7fea56614abce740a47b8`.

- Exported manifest:
  `/Volumes/Transcend/data/EA/inventory-parts/292e4f9c7dfd447097cf8522e405dae8/manifest.json`.
- Manifest SHA-256:
  `17b3ccb36f18387675556374201165ed9c70deec1515ecc9ca930ce5316ebfef`.
- 24 parts total 1,769,830,164 bytes. The first 23 are approximately 75 MB each;
  the final part is 44,838,516 bytes. The largest is 74,999,990 bytes.
- Records: 1,396,813 tree nodes, 1,396,719 unique assets and 27,980 pages.
  All 1,396,720 occurrences and 408,135,188,823 expected payload bytes are retained.
- The existing `/Volumes/Transcend/data/EA-download` ledger now uses its own
  byte-identical pinned set at
  `.pw010-download/inputs/bundles/17b3ccb36f18387675556374201165ed9c70deec1515ecc9ca930ce5316ebfef/`.
  Payload and catalog paths, receipt identities, attempts and frozen selection
  were compared with a pre-attachment baseline and retained.
- A fresh `verify --pilot` verified every inventory part and all 250 existing
  PDFs, with zero failures and all content fully checked. It took 125.052 seconds
  and observed 131,088,384 bytes peak child RSS on this Mac. The pilot JSONL hash
  remains `687aeeebbe50700ead14662a2e4cdb7486e535698eb65757e92a822ea0714262`.
  The current report remains `/Volumes/Transcend/data/EA-download/verify-result.json`.
- Split/attachment/audit logs and split/audit measurements are under
  `/Volumes/Transcend/data/EA/inventory-parts/`. No new archive payloads were fetched.

This confirms the input conversion and the existing pilot. The full archive
payload download is still incomplete; inventory completeness and pilot audit
success do not certify all 1,396,719 originals as downloaded.
