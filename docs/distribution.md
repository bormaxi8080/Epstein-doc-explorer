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
2. Offline builder, verification and data-folder preparation: next.
3. Resumable acquisition/import and Google Drive adapter: next.
4. Reproducible Viewer and processor installation: next.
5. Downloader/OCR operator workflow and portable pilot: next.
6. Full inventory catalogue integration and release activation: next.
7. Distribution acceptance tests and real external release: next.

Only verified milestones will be marked implemented. Live downloads/inference and
public publication remain separate explicit commands.
