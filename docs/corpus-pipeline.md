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
| 15 | Bounded full-corpus operation | Implemented; real acceptance gates below |

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

## Operational commands

Run `.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json COMMAND`.
Config paths are separate from the frozen selection, input inventory, downloader
destination and baseline. Secrets come only from `FCC_API_KEY`. Nothing starts
after import. Commands: init, freeze, import, plan, work, text, analyze-plan,
synthesize, review-page, approve, export, status and preflight. `--help` documents
each argument. Init requires an existing empty external directory. The example
points to the already initialized pilot; do not initialize it again.

`work --limit 500` performs only local inspection/extraction. Model jobs require
`work --stage ocr --limit 250 --allow-fcc` or `--stage analyze`. Every invocation
revalidates the explicitly free image route. There is no paid fallback. A durable
attempt ledger counts calls before sending them, including ambiguous outcomes,
with both invocation and lifetime caps. Manual pilot calls count toward the same
lifetime ceiling. HTTP 429/retryable provider errors pause work and retain jobs;
rerun after the provider's quota window. Never rotate accounts to bypass quotas.
SIGINT/SIGTERM finish the current bounded job then pause. An exclusive corpus
operator lock serializes mutations; do not mix custom API writers with the CLI.
Queue acceptance is fenced; immutable orphan artifacts can remain after crashes.
Logs rotate on the external root and omit document text/credentials. FCC envelopes
are retained externally for diagnosis, including invalid structured responses.

`status` is read-only. Exit 3 means incomplete/paused, exit 2 is an operation
failure, exit 0 is a completed command (only status/work 0 means full approved
coverage). Coverage, review, unsupported formats and failed jobs are separate.
Queue exhaustion alone never means archive completion. Private export explicitly
requires `--include-unreviewed`; public export requires individual approvals.

Operational gates remain: more representative originals (5,000-file strata),
manual OCR/evidence accuracy review, confirmed audio/video conversion routes,
and disk/time/cost projections from measured real pages. The actual inventory has
1,396,719 unique linked assets, not 14 million; PDF page counts are not known from
that inventory. Do not schedule the full archive based on a guessed page count.

Recovery is explicit: `retry-job JOB --reason TEXT --extra 1` grants at most three
additional attempts without erasing past attempts/events or bypassing call caps.
When changing routing/text, replan and use `supersede-job OLD NEW --reason TEXT`
for the same document/page/stage; superseded history is retained separately.

## Integration checks

Analysis plans bind model, text, schema, prompt and fragment budget; changing any
of them cannot silently reuse another plan's roots. Summary artifacts retain
their input lineage. Scratch contexts reserve aggregate capacity, including stale
files. Scheduling uses 5,000-job transactions. ZIP member manifests stay on disk
rather than exceeding worker IPC limits. Export preserves archive receipt/member
lineage even for container wrappers without a text row. API cluster metadata
comes from SQLite in new bundles, and UI year bounds come from actual data.

+Private quality evaluation may explicitly use `analyze-plan --allow-review` and
+`synthesize --allow-review`; the resulting manifest keeps review_required and
+cannot pass public approval. This enables a draft pilot without pretending that
+photo/blank pages have been manually reviewed. Default analysis still requires
+accepted pages. For local private bundle inspection, bind the API with
+`HOST=127.0.0.1`; original payloads are not served by that API.
+
+On 2026-11-16 a synthetic million-job benchmark on the external Transcend root
+scheduled in 13.45 s (74,329 jobs/s), resolved 1,000 jobs in 1.53 s, and peaked at
+39,043,072 RSS bytes. Before indexed claim selection the 1,000 resolutions took
+67.76 s. These measurements cover queue persistence only, not OCR throughput or
+semantic accuracy. No model calls were made by the benchmark.
+
## Continuous progression

`work --allow-fcc --limit N` prioritizes downstream ready jobs and incrementally
plans text, analysis and synthesis after each document/page result. It does not
require one shell invocation per document. Without --allow-fcc it only runs local
stages. Reviewed quality is the default gate; explicit --allow-review retains
draft flags throughout and cannot approve documents. On an existing partially
processed corpus, run `advance --limit N [--allow-review]` once to seed downstream
jobs; its cursor supports bounded pagination. Free call caps and provider pauses
still apply to the entire work invocation. Read-only status distinguishes selected
corpus completion from full_archive_complete and exposes frozen selection scope.

+FCC request_timeout is configurable from 1–300 seconds and max_output_tokens
+from 128–16,384; defaults remain 120 seconds/8,192 tokens. A timeout has an unknown
+upstream outcome and pauses the official worker. Do not run parallel custom model
+drivers against the configured single-slot FCC provider. Changing these request
+parameters changes the provider/cache identity and requires replanning.
+
`init-tags --clusters tag_clusters.json` pins the review dictionary in corpus
state. `propose-tag TAG --allow-fcc` and `propose-alias NAME --candidate NAME
--allow-fcc` create cached proposals only. `review-tag TAG CLUSTER --reason TEXT`
and `review-alias ORIGINAL CANONICAL --reason TEXT` apply explicit decisions.
Exports carry those reviewed decisions into the compatible bundle; unknown or
redacted identities can never be merged by these commands.

+Bundles include per-chunk requested/reported model, prompt, usage, response digest
+and Unicode page-fragment offsets in ingestion_analysis_chunks. Evidence spans
+are stored only for unique quote matches; repeated quotes retain a match count
+and null offsets instead of fabricated coordinates.
+
## Real-provider validation feedback

The authorized draft check exposed a timeout, an additional response property,
and an evidence quote absent from the supplied fragment. Those results were
rejected, never published as accepted analysis. Evidence prompt v2 explicitly
excludes relationships inferred from stamps/filenames/photo labels and permits
one budgeted corrective call for schema/evidence failure. Both answers are kept;
the correction must pass the same strict validator. A second failure remains
failed. Network timeouts still pause instead of triggering a corrective call.
