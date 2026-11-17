# Changelog

Changes to this fork are recorded here; upstream history remains in Git.

## Unreleased

### Added

- Register the operator-supplied public Google Drive folder, verify its manifest
  against the publisher pin, and map all 33 listed payloads to Drive file IDs.
  Document browser/offline import and the separate authenticated API path.

## 1.2.0 — 2026-11-17

### Fixed

- Count preserved Viewer records without calling them available text; show an
  explicit missing-text state for the three unchanged upstream rows with no text.

- Escape document-derived graph tooltip markup and keep catalogue dialogs above
  mobile navigation; verify folder/search behavior in desktop and mobile Chrome.

- Bulk-load catalogue rows before building unique/FTS indexes, keep construction
  journals in unpublished external scratch, and verify/reuse frozen gzip transports.

- Allow identified reviewers to submit bounded UTF-8 page corrections, retaining
  correction hashes/previous versions and invalidating text, analysis and approval.

- Give evidence correction a bounded previous answer and precise invalid-field paths
  in prompt v4. Pin existing plan versions during resume; explicit per-document
  upgrades preserve other successful analyses and approvals.

- Remove stray patch markers from the corpus workflow guide.

### Added

- Distribution stage 9: verified real public inventory/starter and separate private
  250-file OCR pilot packages on the external disk, publisher-pinned descriptors,
  upload checklists and relocation/API/original-hash acceptance evidence.

- Distribution stage 7: explicit complete profile, offline upload/source-map binding,
  full-catalogue container acceptance and reusable public/private external data releases.

- Distribution stage 6: complete inventory SQLite/FTS catalogue, bounded folder and
  source search, explicit text/review coverage, source-to-member document mapping,
  verified relative release activation/rollback and catalogue disk identity guards.

- Distribution stage 5: local workflow preparation, portable reviewed-source/OCR
  exports and offline pilot adoption with preserved receipts and a fresh audit.

- Distribution stage 4: digest-pinned Viewer/processor images, minimal server
  dependencies, hashed Python locks, explicit Compose profiles and read-only doctor.

- Distribution stage 3: pinned HTTPS/Drive source maps, strong-ETag resume,
  explicit provider cooldowns, credential-safe redirects and offline relocation.

- Distribution stage 2: bounded gzip release builder, standalone database and
  inventory-pair checks, whole/parts semantic verification and private draft gates.

- Distribution stage 1: portable release schema, external workspace role map,
  path/collision/version validation and missing-volume regression coverage.

- Verify zero request/image/reasoning/cache prices as well as prompt/completion
  prices before admitting the explicit free FCC route.

- Bound API SQLite scratch in memory by default; validate an explicit external
  SQLITE_TMPDIR for larger queries and pin its directory identity.

- Publish bundle databases in DELETE journal mode so read-only consumers do not
  need WAL/SHM sidecars inherited from the source baseline.

- Renew active leases during long page/synthesis work and model callbacks;
  verify a simulated 1,000-second assembly without an expired completion lease.

- Evidence prompt v3 requests data instances rather than schema declarations;
  retire obsolete prompt jobs with history. Work limits now count every claimed
  job, including failed jobs, instead of counting successful results only.

- Complete the authorized 250-file private pilot: 250 OCR pages, matching TXT/SQLite
  drafts and validated document analyses. Verify the new immutable bundle and all
  15 draft evidence quotes through the local API; retain human review gates and
  unchanged originals/upstream data. Record bounded provider recovery and paths.

- Keep Office ZIP containers explicitly unsupported instead of treating internal
  XML as completed page OCR; include GIF raster input.

- Keep SQLite setup/temporary work on the external root, avoid DISTINCT
  scheduling spill, and stream read-only status counts with bounded memory.

- Keep draft text review state across replanning and count pending OCR separately
  from pages whose completed transcription requires review.

- Preserve cyclic upstream alias chains as review issues in new corpus bundles,
  leave their names unresolved, and keep the original baseline unchanged. Retain
  source edges with missing documents in an explicit issue table, without
  fabricated documents or disabled foreign-key checks.

- Preserve bounded HTTP error diagnostics and bundle coverage; verify that two
  invalid evidence answers never create an accepted analysis artifact.

- Evidence prompt v2: retain rejected real-provider outputs and permit one
  budgeted schema/evidence correction without weakening quote/date validation.

- Integration hardening: versioned analysis plans across model/chunk changes,
  synthesis input lineage, aggregate scratch reservations, disk ZIP manifests,
  alias/API consistency, data-derived UI date ranges, same-origin local CORS,
  targeted retries and indexed million-job claim selection.

- Corpus stage 15: explicit external-root workflow CLI, verified free-only FCC
  admission, durable call caps, cooperative pause, rotating logs and read-only
  coverage/status gates. Preserve invalid provider envelopes for diagnosis.

- Corpus stage 14: isolated UTF-8/image processors and bounded ZIP members with
  stable identities/lineage; explicit unsupported media states and scale gates.

- Corpus stage 13: immutable compatible TXT/SQLite/FTS export, page provenance,
  evidence and document catalogue UI, including zero-edge documents. SQL filters
  precede bounded relationship results; remove hardcoded archive date exclusions.

- Corpus stage 12: disk-backed graph distances, stable cluster assignments, cached
  semantic tag proposals and reviewed aliases with document-scoped unknowns.

- Corpus stage 11: bounded resumable evidence fragments, adaptive splits, cached
  document synthesis and explicit version-bound approval.

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
