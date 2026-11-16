# Changelog

Changes to this fork are recorded here; upstream history remains in Git.

## Unreleased

### Added

- Corpus stage 10: streaming canonical UTF-8 TXT, hashed Unicode page maps,
  immutable text versions, exact coverage and downstream invalidation checks.

- Corpus stage 9: page OCR, provenance, native/OCR routing and explicit quality
  review, including empty/photo pages, layout bounds and disagreement checks.

- Corpus stage 8: explicit multimodal FCC Messages transport, bounded payloads,
  safe errors/Retry-After and real loopback image-contract regression coverage.

- Corpus stage 7: pinned isolated PDFium/Pillow page extraction and rendering,
  native-layer quality routing, geometry and explicit pixel/output budgets.

- Corpus stage 6: resumable source registry, content-based document deduplication,
  page identities and checked original access without long downloader locks.

- Corpus stage 5: identity-checked external roots, immutable hashed artifacts,
  safe scratch/environment scoping and explicit disk/memory/input budgets.

- Corpus stage 4: persistent leased jobs, stale-worker fencing, delayed bounded
  retries, immutable inputs and restart-safe accepted-result caching.

- Corpus stage 3: scoped, checksummed pilot/full handoffs with read-only archive
  access, receipt/original verification, preserved placements and atomic publication.

- Corpus stage 2: read-only Explorer baseline schema/hash inspection and explicit
  LFS materialization into external storage, without replacing the tracked pointer.

- Corpus stage 1: versioned source/page/OCR/text contracts, stable document IDs,
  parameter-sensitive stage cache keys and a tracked implementation checklist.

- Lossless, checksummed 75 MB JSON inventory partitions with offline split/read-back
  verification and independent `--manifest` downloader inputs. Preserve origin
  identity, hierarchy/order, every asset/page/occurrence and existing pilot files.
- Resumable part pinning and batched SQLite import; bundle-aware full source audit,
  metadata backup/restore, missing-ledger rebuild and audited downstream adoption.
  Failed source verification now invalidates previous successful audits.
- Convert the completed local snapshot into 24 verified parts (1,769,830,164
  bytes), attach it without moving originals or changing pilot state, and pass a
  fresh offline audit of every part plus all 250 previously downloaded PDFs.

- Complete the authorized 250-PDF Data Set 1 pilot with normal age-confirmed
  session access, matching source sizes/ETags, resumable stop and fresh scoped
  SHA-256/PDF/placement audit. Record the external artifact locations and evidence.

- Frozen first-in-tree download pilots with scoped fetch/audit, durable membership,
  readable logical original paths, external event logs and ordinary browser-session
  cookie-file support. Keep full-inventory completion distinct from pilot audits.
- Bound native monitor recovery, compare strong inventory/response ETags, support
  HTTP-date Retry-After and improve external-disk planning with a bounded SQLite
  page cache and bulk-built node index.

- Explicit adoption checks bind receipts to the same source snapshot, asset and
  original path, even when another receipt describes equal bytes.
- Self-contained loopback archive/FCC test runner with inherited external-socket
  guard; CI covers Python 3.10/3.12, lint, format, corpus generation, planner
  smoke checks and API types. Larger synthetic CI benchmarks are manual only.

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
