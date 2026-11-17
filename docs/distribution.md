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
5. Downloader/OCR operator workflow and portable pilot: next.
6. Full inventory catalogue integration and release activation: next.
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
