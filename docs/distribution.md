# Distribution and reproducible installation

The source repository, transport releases and working state have separate roles.
The source version follows VERSIONING.md; data releases bind a frozen inventory
snapshot and a minimum application version. Dataset IDs do not change document
content IDs. Existing upstream files and historical records remain unchanged.

## Release contract v1

`release.json` follows `distribution-v1.schema.json`. Every artifact has a role,
profile group, relative transfer/install path, compression, transfer SHA/bytes
and decoded SHA/bytes. Sizes bound extraction. Duplicate and case-colliding paths,
path traversal, symlinks and incompatible application versions are rejected.
`SHA256SUMS` also binds release.json; pin its digest in the source release catalogue.
A checksum fetched from the same untrusted location is not an independent trust anchor.

Viewer, inventory-full, pipeline (parts plus source DB), and pilot profiles select
only required artifact groups. A private release requires an explicit draft
opt-in. The inventory's complete flag describes the catalogue, not processing.
Working downloader/OCR databases are local state, not ready-to-publish input DBs.

## Storage

`examples/workspace.json` is a portable role map. Set root to an existing absolute
external directory before initialization. The application never creates a missing
volume root. Managed subdirectories stay under that root; the disk identity and
free reserve are checked. Installation must not initialize or fetch implicitly.

## Implementation milestones

1. Release contract and portable workspace: implemented.
2. Offline builder and semantic verification: implemented; real data folder is prepared after acceptance.
3. Resumable acquisition/import and Drive file adapter: implemented; real shared-folder validation awaits an operator link.
4. Native/container installation and read-only doctor: implemented; acceptance records follow below.
5. Workflow config generation, portable private pilot and fresh-audit adoption: implemented.
6. Full inventory catalogue integration and release activation: implemented.
7. Distribution acceptance tests and real external release: next.

Only verified milestones will be marked implemented. Live downloads/inference and
public publication remain separate explicit commands.

## Offline builder

`explorer-data build --recipe /external/recipe.json --output /external/new-release`
creates an immutable release and independently decodes/verifies it before rename.
Recipe artifacts explicitly list absolute source paths and portable target paths;
recipes with machine paths stay outside Git. Gzip inputs require their expected
uncompressed size. `explorer-data verify /external/release` repeats checks.
Private builds/verification require `--allow-drafts`. Producer SQLite is evidence;
JSON remains authoritative. Matching snapshot/counts are checked, alongside full
JSON/parts semantic signatures when both are present. Public viewer databases
must contain no pending imported documents and match their bundle manifest/TXT.
All build and verification scratch is on the selected external filesystem.

## Obtain and relocate a release

First obtain the small release.json and its publisher-pinned SHA from the source
release catalogue. The source map is separate from the immutable data manifest:

```json
{"format":"explorer-release-sources","version":1,"release_sha256":"PUBLISHER_SHA256",
 "artifacts":{"ARTIFACT_ID":{"drive_file_id":"UPLOADED_FILE_ID"}}}
```

A locator may instead use `{"url":"https://your-mirror/path"}`; explicitly admit
custom hosts with `--allow-host`. The Drive adapter uses the official media API;
set EXPLORER_DRIVE_TOKEN through your secret mechanism if access requires OAuth.
Tokens/resource keys are never stored in partial metadata. Cross-origin redirects
strip authorization. Browser-downloaded files can go directly through offline import.
No shared-folder URL is available yet; real Drive behavior has not been tested.

```bash
explorer-data fetch --manifest /external/release.json --expected-sha256 PUBLISHER_SHA256 --sources /external/sources.json --destination /external/empty-package --profile pipeline
explorer-data import /external/package --output /external/new-install --profile pipeline --expected-sha256 PUBLISHER_SHA256
explorer-data verify-install /external/new-install
```

Fetch is the explicit network operation. A pause exits 3 and reports cooldown;
rerun the same command afterward. There is no hidden retry loop. Strong ETags
permit validated-prefix Range/If-Range; a 200 fallback restarts cleanly. HTML,
incorrect ranges and hashes fail closed. Corrupt prefixes are retained separately.
Partial metadata is under .download-state, outside payload namespaces. Private
pilot acquisition/import requires --allow-drafts and remains unreviewed.
Install creates relative data references, not a copied machine-bound work queue.

## Prepare a local processing workflow

After importing pipeline data and initializing workspace roles:

```bash
explorer-data prepare-workflow --workspace /external/workspace.json --installation /external/inventory-install --model claude-3-freecc-no-thinking/open_router/inclusionai/ling-3.2-flash:free --routing-revision your-reviewed-route
```

This writes archive.json, corpus.json and structured command arguments under
workspace/configs/RELEASE_ID. It neither initializes processing state nor starts
network/model work. A different existing configuration is rejected rather than
silently changing cache identity. Read [the operator workflow](operator-workflow.md).

## Portable private pilot

`explorer-data export-pilot --selection /external/selection/selection.json
--corpus /external/corpus --output /external/new-portable-pilot` exports checked
source rows, original receipts, canonical page/structured OCR and validated
analysis in four bounded JSONL files. Only a frozen pilot (at most 1,000 assets)
is admitted. Queue leases, local roots, credentials and raw HTTP envelopes are
not exported. Originals are included by the later release recipe under the same
relative paths. Export requires an idle corpus writer. The selection header binds
membership, hashes, page coverage and review state. Pilot groups must be private.

On another machine, create a matching local download plan/layout, then use
`explorer-download --destination /external/new-archive import-pilot
/external/installed/pilot` to copy/adopt originals, preserve original receipts and
perform a new scoped offline audit. No source GET is made. Re-freeze selection
for the new local archive before starting OCR. Ready Viewer data is reusable
without inference; fresh processing intentionally creates local jobs instead of
copying machine-bound queue state. Original receipt times are historical origin
evidence; the new bundle-import event records the local adoption separately.

## Full archive catalogue and activation

`explorer-data build-catalog --manifest /external/inventory/manifest.json
--output /external/inventory.sqlite3` builds a bounded-memory read-only index.
The original full JSON and partition files remain authoritative and unchanged.
The index retains every logical node, source occurrence and additional record;
FTS searches filenames/URLs, not OCR text. SQLite sort scratch stays on the chosen
external filesystem. Include it as role catalog-db, group catalogue, installed as
viewer/inventory.sqlite3 beside the Viewer database. Viewer and pipeline profiles
include catalogue when available. A public starter contains only existing upstream
text and approved imported text; a private pilot remains visibly pending review.

The Viewer discovers inventory.sqlite3 beside DB_PATH, or accepts an explicit
INVENTORY_DB_PATH. A missing explicitly configured catalogue fails startup;
removal/replacement during service returns 503 rather than silently serving stale
coverage. Archive browsing and searches are cursor-paginated and capped at 100.
Document/source mappings include ZIP descendants; older small pilot bundles have
a bounded legacy mapping. No filename-based matching to upstream text is inferred.
The graph remains a bounded view of published relationships, not a claim that all
source files have been processed. Media without a supported processor remains
explicitly unsupported in the processing ledger.

Import a Viewer installation directly under the workspace releases role, then:

```bash
explorer-data activate --workspace /external/workspace.json --installation /external/releases/RELEASE_ID
```

This verifies content, bundle pairing and the selected profile before atomically
switching releases/current to a relative target. Mount the whole releases folder
at /data in the Viewer and restart after activation. Roll back by activating the
previous installation. Existing directories/escaping pointers are rejected.
Private installation/activation requires --allow-drafts; do not expose a private
pilot as the public default. Original source downloads remain under the downloader
root; the catalogue provides the official source URL without initiating a download.
