# Corpus pipeline implementation

The archive input is a frozen PW-010 inventory, not a model context. Originals,
the original JSON and the downloader ledger retain their existing locations.
New derived data must live outside Git, on an explicitly selected external root.

## Contracts

Corpus artifacts use `corpus-v1.schema.json`. A physical document retains the
existing `DOJ_<first 40 source SHA-256 hex digits>` identity; the complete SHA is
also stored and collisions must fail. OCR changes do not change document IDs.
Cache keys include the stage, full source hash, page and complete parameters.
Artifacts distinguish coverage, quality, analysis completion and review status.

An OCR artifact is a transcription with typed blocks, optional normalized
0–1000 bounding boxes and review flags. It is not an analysis or approval.
Document text uses UTF-8 and `[Page N]` markers. Page maps retain exact character
offsets. The same serialized text must populate TXT and `documents.full_text`.

## Implementation stages

| Stage | Scope | State |
| --- | --- | --- |
| 1 | Contracts and stable identities | Implemented |
| 2 | Real upstream baseline and compatibility checks | Implemented; real materialization recorded separately |
| 3 | Verified pilot/full source selection | Implemented |
| 4 | Durable leased job queue | Implemented |
| 5 | External storage and resource limits | Implemented |
| 6 | File/document/page registry | Implemented |
| 7 | Native extraction and isolated rendering | Implemented |
| 8 | Multimodal FCC transport | Implemented; live route requires explicit pilot |
| 9 | OCR, quality checks and pilot | OCR implemented; live pilot recorded separately |
| 10 | Canonical text and page maps | Implemented |
| 11 | Chunk analysis and document assembly | Implemented |
| 12 | Tags, entities and graph | Implemented |
| 13 | Compatible export, API and UI | Implemented |
| 14 | Other formats and scale validation | Implemented for image/text/ZIP; media routes gated |
| 15 | Bounded full-corpus operation | Pending |

Every implemented stage has regression tests and an Unreleased changelog entry.
Network/model operations are explicit; offline tests use synthetic loopback
services. Public export still requires document approval. No full-corpus
completion can be claimed while sources, pages or required stages are pending.

## Verified upstream baseline

The pinned LFS object was materialized on 2026-11-16 at
`/Volumes/Transcend/data/EA-explorer/baseline/document_analysis.db`. It is
279,040,000 bytes, SHA-256
`a26cfa2d0daa17d32c0d884b6e13062009a9d473d96268c3cd8a77136b0c7c26`,
with 25,232 documents and 107,030 RDF triples. Read-only quick_check and required
Explorer columns passed. The tracked LFS pointer was retained unchanged.

## Source handoff

`freeze_sources` takes a short shared archive lease and requires a successful
matching pilot/full audit. It verifies ledger, receipt, membership and original
hashes, then publishes `sources.jsonl` and `selection.json` atomically in a new
derived directory. It preserves every source occurrence and copies no originals.
The frozen selection is independent of later unrelated archive downloads; each
original is still checked before processing. Network/model calls never retain
the downloader lease. A changed frozen selection is rejected.

## Job durability

Jobs have immutable inputs, bounded attempts and fenced expiring leases. Each
claim uses `BEGIN IMMEDIATE`; stale workers cannot publish accepted results.
Successful work is cached across restarts. Retries are delayed and exhausted
jobs remain failed, never complete. This prevents duplicate accepted artifacts,
not duplicate upstream charges after an ambiguous network timeout. SQLite is
the initial single-machine queue; workers do not retain archive leases.

## Storage admission

Initialize an existing empty external directory. The volume, root marker and
opened queue inode are checked; missing roots are never recreated. Artifacts
are content-addressed, durable and immutable. Scratch/environment overrides are
scoped to the selected root. Defaults reserve 5 GiB, bound artifacts to 16 MiB,
scratch to 512 MiB, source documents to 512 MiB and rendered pages to 20 million
pixels. Parser workers additionally enforce time/RSS limits. These are admission
guards, not filesystem quotas. Credentials and rendered images are not logged.

## Registry

Frozen selections import in 5,000-source transactions; only a completely
validated selection can schedule inspection jobs. Exact content hashes share
physical document processing while every asset/occurrence remains in source
metadata. Page rows use `(doc_id,page)` identities. Source reads use short
read-only ledger access and verify the original hash on first use or stat change;
an eight-entry worker cache avoids rehashing an unchanged PDF for every page.
Root identity and file stamps are checked again after each operation.

## PDF workers

PDFium 5.15.2 and Pillow 12.3.0 are pinned. Each inspection/extraction/render is
a disposable process with CPU, wall-time, RSS and file-size budgets. Parallel
PDFium work uses processes, never simultaneous threads. The parent only reads
bounded metadata. Rendering records source geometry, intrinsic rotation, DPI
and dimensions. Native quality detects stamp-only, short, repetitive and broken
layers; this heuristic is routing evidence, not an OCR accuracy guarantee.

## FCC operations

`FCCMessages` uses explicit model/routing configuration, image blocks, bounded
responses and no tools or redirect credential forwarding. The queue owns retries
and call admission; the transport performs one attempt. Image/response bytes are
limited, truncation and invalid JSON fail closed, and HTTP Retry-After is bounded.
API keys stay in process environment, outside config/artifacts. Requested and
reported models, response IDs, usage and response digests are retained.

## OCR and review

OCR preserves raw native and model results separately. Schema, layout bounds,
body coverage and native/OCR disagreement determine review flags. Photo/blank
classifications require explicit review; HTTP success never auto-approves a page.
Accepted pages carry full source/render/model/prompt provenance. Page edits or
review decisions invalidate document text/analysis/approval. Rendering is bounded
just-in-time scratch, not a persistent cache of every page. Network calls only
run in explicitly invoked OCR jobs after budget admission.

## Canonical text v2

TXT uses exactly `[Page N]\n` markers and two newlines between pages. A streaming
JSONL page map records Unicode code-point offsets and the immutable page
artifact reference. V2 uses a hashed external map to avoid loading a large
document's complete page map into RAM. Both files publish before the text
manifest/DB pointer; incomplete or unreviewed pages cannot silently disappear.
Private draft assembly can explicitly retain review-required pages. Reassembly
is idempotent; changed page provenance invalidates analysis and approval.

## Analysis and synthesis

Fragments have a conservative UTF-8 byte budget, not an asserted tokenizer count.
Exact evidence is checked against each fragment. Truncated/capacity-limited output
splits deterministically into smaller durable jobs. Assembly requires all leaves.
Bounded rolling document synthesis consumes every fragment summary and caches
each merge; it cannot introduce relationships. The original chunk evidence stays
in a streaming hashed map. This is hierarchical compression, not a guarantee of
lossless semantic recall. Public approval binds the current text/analysis version.

## Graph and tags

Cluster IDs stay stable. Unknown tags enter Unclassified; optional FCC proposals
are cached and require explicit review before changing assignments. Aliases need
reviewed reasons, reject cycles, and cannot merge unknown/redacted identities.
Adjacency and BFS frontiers use SQLite temporary tables with disk-backed temp
storage under the corpus root. No full graph is loaded into Python memory.
Disconnected entities retain the legacy hop-distance sentinel 1000.

## Explorer compatibility

`export_corpus` writes a new external immutable bundle from the unchanged baseline.
Canonical TXT is streamed into `documents.full_text` (Python 3.11+ blob I/O for
large documents). IDs, legacy fields/JSON arrays and the 30 original cluster IDs
remain compatible. Page text/provenance, evidence and FTS5 are additive tables.
The default export selects approved versions; explicit private exports retain
pending OCR/analysis and show review status. No original payloads are published.

The catalogue searches text and paginates all documents, including zero-edge
documents. Page/evidence endpoints and the catalogue UI keep responses bounded;
legacy text is a 200,000-character preview. Relationship predicates now run in
SQLite before LIMIT, removing the silent 100,000-row pre-filter cap and fixed
1970–2025 exclusion. Result limits count triples, with explicit hasMore metadata;
the old density-based edge selection is replaced by stable date/ID ordering.
No claim of complete interactive recall applies to a bounded graph window.

## Additional formats and validation scope

Isolated parsers handle UTF-8 text and raster images (including bounded TIFF
frames). ZIP expansion checks traversal, symlinks, encryption, duplicate names,
CRC, entry count, compression ratio, aggregate bytes and nesting depth. Members
receive content identities and parent/member provenance, then enter inspection.
Images are transiently converted for the same OCR route. DAT/OPT are retained
as native UTF-8 text; reconstructing vendor-specific record relationships needs
a representative source fixture and is not claimed by the generic text parser.

Audio/video/Office formats remain explicitly unsupported until a compatible
ASR/conversion contract and free FCC route are verified; they cannot count toward
completed OCR. FCC's current text/image contract is not an audio API. Synthetic
tests include more than 100,000 graph rows and malformed container budgets.
Only 250 real PDF originals are locally available; a 5,000-file representative
acceptance run remains an operational gate, not synthetic evidence of accuracy.
