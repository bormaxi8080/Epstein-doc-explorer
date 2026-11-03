# Changelog

Changes to this fork are recorded here; upstream history remains in Git.

## Unreleased

### Added

- Metadata backup/restore destination identity checks and durable nested-directory
  publication prevent fallback writes after storage replacement.

- Read-only `status --watch`, local `doctor` and opt-in filesystem probes.
- Checksummed metadata backup/restore and missing-ledger recovery from frozen
  inventory plus receipts; original bytes are preserved and restored audits
  are invalidated before any ingestion handoff.
- Measured million-asset synthetic planning (186 s, about 39 MB peak RSS).

- Offline `adopt-archive` links a complete audited corpus to a separate ingestion
  root without copying originals; extraction checks source leases, audit bindings
  and hashes while retaining every original occurrence.

- Streaming 100k/million-asset benchmark tooling, bounded planner caches/indexes,
  and generated synthetic document/scan/table/TIFF/ZIP corpora.

- Isolated format validators with wall/CPU/RSS limits, parent-death cleanup,
  resumable operator stop and subprocess crash tests at durability boundaries.

- Versioned archive artifact schemas, v1 compatibility fixtures, safe-path and
  semantic completeness checks at publication/consumption boundaries.

- Standalone `scripts/download_archive.py` / `explorer-download` commands for
  offline planning, explicit acquisition, offline verification and quarantine.
- Independent JSON, optional read-only evidence DB and external-disk destination;
  pinned PW-010 v1/v2 schemas, streaming registry/hierarchy/count validation.
- URL-based originals, complete logical placement references, resumable strong-ETag
  Range transfers with prefix hashes, receipts and publication recovery.
- Per-file/disk/retry budgets, HTML rejection, PDF/ZIP/text checks, media-check
  distinctions, offline SHA-256 audits and a downstream verified JSONL manifest.
- Synthetic regression fixtures and local Postman archive simulation; no real
  archive fetch or OCR/model processing was run.

## 1.1.0 - 2026-11-02

### Added

- Independent project rules, version policy, and Codebase Memory MCP setup with
  a best-effort post-commit refresh hook.
- Resumable PW-010 v1/v2 inventory import, provenance and content-addressed originals.
- Explicit bounded downloader, ZIP expansion, UTF-8 text/PDF extraction and
  `needs_ocr` / unsupported states.
- FCC Messages adapter, bounded retries, exact page-quote validation, versioned
  chunk caching and explicit document approval.
- Atomic Explorer database/cluster bundle export over an optional upstream
  SQLite baseline, page evidence, reviewed aliases and graph hop recalculation.
- Synthetic regression tests, a local Postman FCC simulation and scoped API typechecking.
- Read-only serving of existing published databases by default and a configurable
  cluster path, with a Docker/Caddy self-hosting scaffold.

### Scope

- No live DOJ download or NVIDIA invocation is performed by installation/tests.
- OCR, DAT/OPT reconstruction, nested archives, audio/video transcription,
  semantic cluster assignment, evidence UI and large-corpus API pagination
  remain planned. See `docs/ingestion.md` for current limits.
