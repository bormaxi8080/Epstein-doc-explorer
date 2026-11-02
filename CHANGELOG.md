# Changelog

Changes to this fork are recorded here; upstream history remains in Git.

## Unreleased

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
