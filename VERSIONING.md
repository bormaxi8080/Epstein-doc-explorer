# Versioning

The fork uses Semantic Versioning independently of Oriol and upstream releases.
Initial fork version: **1.1.0** (additive ingestion capability over upstream 1.0.0).

`package.json`, its root entry in `package-lock.json`, `pyproject.toml`, and
`ingestion/explorer_ingest/__init__.py` must agree. `network-ui` retains its
independent upstream package version until a UI release changes it.

- Patch: compatible fixes and maintenance changes.
- Minor: compatible ingestion stages, commands, optional fields or UI features.
- Major: incompatible CLI, data contracts or published database behavior.
- Describe each committed behavior change under `Unreleased` in CHANGELOG.
  Creating a commit does not by itself require a release or tag. When cutting
  a release, move reviewed entries to a dated version section and bump all
  version authorities together. Do not rewrite upstream history.

Data versions are independent: PW-010 schema v1/v2, extraction version,
analysis schema version, prompt version, FCC routing revision, archive layout/receipt
format (currently 1), bundle format,
and cluster-file SHA-256. Changes to any analysis input invalidate its cache.
Increment `FCC_ROUTING_REVISION` whenever FCC routing or fallback changes.

Published bundle directories are immutable. Build a new directory, validate it,
then activate it as a unit; database and clusters must never be mixed across
versions. Preserve source inventories and originals for reproducibility.
