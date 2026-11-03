# DOJ ingestion: preliminary implementation

This fork keeps the original Explorer application and adds an independent Python
pipeline. PW-010 currently inventories DOJ links and metadata; it does not fetch
archive payloads. The sibling Oriol project is an input producer, not a runtime
dependency. No action runs implicitly after installation.

## Bulk archive acquisition

Use the independent [archive downloader](archive-downloader.md) for the final
PW-010 corpus. It has separate JSON/optional DB/destination paths, persistent
resume, source hierarchy and offline audit. The small `explorer-ingest download`
command below remains a backwards-compatible pilot helper. Archive JSONL-to-OCR
adoption is a future adapter; no command automatically starts analysis.

## Implemented stages

1. Stream PW-010 v1/v2 `assets` with `ijson`. Check certification flags, issue and
   asset counts, source identity, HTTP(S) URLs and sizes. Preserve the complete
   original inventory by SHA-256, including tree/pages and every occurrence.
   This is consumer-contract validation, not a claim to reimplement all of
   PW-010's JSON Schema or its completeness certification.
2. Explicitly download at most `--limit` assets per invocation. Allow DOJ hosts
   by default; redirects must remain on allowed hosts. Enforce byte and socket
   timeout limits, verify advertised size when present, record content SHA-256,
   and publish the original only after a complete transfer. Failed assets retry
   on the next invocation. Interrupted transfers restart; HTTP Range resume is
   not implemented.
3. Expand bounded ZIP archives into content-addressed files. Reject traversal,
   symlinks, excessive expansion and entry counts (`extract --max-bytes` and
   `--max-members`). Extract UTF-8 TXT/Markdown/CSV
   or PDF text per page. Any PDF page with fewer than 20 non-whitespace characters
   marks the entire document `needs_ocr`; this deliberately conservative heuristic
   also flags blank pages. Preserve unsupported/empty/failed documents explicitly.
   Identical bytes share a document; every asset/member occurrence remains linked.
4. Analyze page fragments through FCC's non-streaming Messages endpoint. Validate
   JSON Schema, real full dates and exact quotes in the supplied fragment. Cache
   each successful chunk independently of document completion. Responses retain
   requested routing, reported model, usage and prompt version. A response model
   string is not independent proof of FCC's actual upstream backend.
5. Explicitly approve a successful document after reviewing its stored analysis
   against the original. Reanalysis invalidates approval. Matching evidence text
   alone does not prove a relationship or establish factual truth.
6. Export a new immutable bundle: `document_analysis.db`, `tag_clusters.json`,
   `manifest.json`. Optionally back up an upstream baseline through SQLite's
   read-only backup API. Never modify the tracked baseline. Preserve original
   rows, add source/evidence/provenance tables, reviewed aliases, document-scoped
   unknown people and recomputed hop distances. Recompute clusters as one bundle;
   unknown tags enter a deterministic additional `Unclassified` cluster without
   changing existing IDs. Check SQLite integrity and foreign keys before publish.

## Local upstream data

The cloned `data/` corpus is retained unchanged. The current checkout's
`document_analysis.db` is a 134-byte Git LFS pointer, not the 279,040,000-byte
SQLite object. Git LFS is not currently installed in this shell. Fetch and verify
that object before starting the original corpus or passing it as `--base-db`;
synthetic tests do not establish real-corpus readiness.

## Installation and commands

Use Python >=3.10. Run from the fork root:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
git lfs pull --include=document_analysis.db
```

The upstream database is a Git LFS object. The exporter rejects a pointer file.
Keep large data outside the checkout; commands reject an internal data root.
Example paths below are operator choices, not files created by installation:

```bash
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion import-inventory /path/to/doj_disclosures.json
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion status
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion download --limit 10
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion extract --limit 10
```

Partial catalogs require `import-inventory --allow-partial` and remain marked
partial. Download, extraction and analysis completeness are separate from catalog
certification. An exclusive process lease prevents competing writers on the
same data root. The ledger uses SQLite WAL; copy state only while stopped or use
SQLite's backup API, not a raw copy of an active database.

## FCC configuration and dependency contract

Configure the NVIDIA key inside FCC. The ingestion process needs only proxy
configuration. Do not commit real keys or put secrets into command arguments.

```bash
export FCC_BASE_URL=http://127.0.0.1:8082
export FCC_MODEL=your-configured-model-id
export FCC_ROUTING_REVISION=your-routing-configuration-version
# Set FCC_API_KEY through your shell's secret mechanism if proxy auth is enabled.
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion analyze --limit 10
```

The base URL excludes `/v1/messages`; an optional base-path prefix is supported.
Port 8082 is an example, not discovery of your running FCC. Non-loopback HTTP is
rejected; remote endpoints need HTTPS. HTTP redirects are never followed with
credentials. Requests send `model`, `max_tokens`, `stream=false`, and one user
message. HTTPS certificate validation uses the certifi CA bundle by default;
set `SSL_CERT_FILE` to a reviewed private CA bundle when required. TLS validation
is never disabled. Requests contain no tools. Headers include `anthropic-version: 2023-06-01` and, when
configured, bearer and x-api-key proxy authentication. Responses need text
content blocks, valid analysis JSON and `stop_reason=end_turn`. Truncation,
unexpected blocks or invented evidence fail closed. 429/selected 5xx/transport
errors retry at most twice; sleeps and Retry-After are capped at ten seconds.
Calls after a timeout can consume additional provider tokens.

Default fragments are 16,000 characters with overlap and a 4,096 output-token
cap, not a guaranteed tokenizer-aware context budget. Chunk summaries are joined
deterministically; global document synthesis is not implemented. Increment the
routing revision whenever FCC model mapping, fallbacks or request transformation
changes. Disable transformations that remove quoted evidence. Cache identity
includes text/source/extraction, schema, prompt and requested routing parameters.

Inspect `responses/*.json` and the document's `analysis_json` in the external
ledger. The review command deliberately requires one document ID:

```bash
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion approve DOJ_DOCUMENT_HASH
.venv/bin/explorer-ingest --data-root /path/to/ea-ingestion export /path/to/releases/pilot-001 --clusters tag_clusters.json --base-db document_analysis.db
```

`--include-unreviewed` is an explicit private-pilot override. Stock Explorer UI
does not display review status; never use that override for a public release.
`--aliases reviewed-aliases.json` accepts a list of `original_name`,
`canonical_name`, `reasoning`; it does not ask a model to merge identities.
Export from the unchanged upstream baseline each time; an already augmented
baseline with colliding DOJ IDs is rejected.

## Published data

`ingestion_documents` records collection/review/model provenance;
`ingestion_sources` retains every inventory/asset/member link;
`ingestion_evidence` joins graph triples to exact page quotes and assertion type.
Upstream RDF field names and JSON-array encodings remain compatible. Unknown
people are scoped by document ID, and disconnected entities use hop distance
1000. Original bytes remain in the external data root; the bundle manifest
explicitly says `originals_included=false`. A `file_path` is a portable reference,
not an implemented original-file serving endpoint.

## Verification

```bash
PYTHONPATH=ingestion .venv/bin/python -m unittest discover -s ingestion/tests -v
npm run typecheck:api
(cd network-ui && npm run build)
postman simulation run postman/simulations/fcc.sim.yaml --port 4010 --no-history --output ndjson
# In another terminal:
FCC_TEST_BASE_URL=http://127.0.0.1:4010/fcc PYTHONPATH=ingestion .venv/bin/python -m unittest discover -s ingestion/tests -v
```

The tests use synthetic inputs and exercise the real HTTP adapter against the
local Postman stand-in. They cover approval/cache invalidation, baseline
preservation, evidence/date rejection, ZIP traversal/deduplication, PDF OCR
handoff, bounded download, truncation, timeouts, 429 and 5xx behavior. Inspect
simulation request events as well as test results. Logs contain synthetic text
and stay outside Git. The user's installed FCC/NVIDIA behavior remains a live
pilot check; no provider-owner mock was reused for this local compatibility
subset. The stand-in is defined from the caller contract and public FCC docs.

## Planned work before a full archive/public deployment

- Full schema coverage and performance validation on the actual certified dump.
- Conditional/Range downloads, response metadata receipts and content-version
  tracking across snapshots; authoritative identity mapping when available.
- OCR and layout quality, DAT/OPT reconstruction, nested archives, media and
  Office formats; large-file streaming extraction and storage admission control.
- Tokenizer-aware grouping across pages, document synthesis, semantic tag
  assignment, assisted alias review, evidence span coordinates and review UI.
- Original-page serving and collection/evidence indicators in the frontend.
- Replace the upstream API's 100,000-row pre-filter cap and in-memory filtering
  before claiming complete large-corpus search; review its fixed date limits.
- Validate the real upstream LFS database and run a reviewed 100–300 document
  pilot through the user's FCC before choosing a production model/host size.

## Preliminary verification record (2026-11-02)

- Python package installed in an isolated Python 3.12 environment.
- Seventeen synthetic regression/HTTP tests passed with the local Postman simulation.
- API `npm run typecheck:api` and frontend `npm run build` are the scoped checks.
  The full checkout's `npx tsc --noEmit` reports existing type errors in unchanged
  `test_qwen_embedding.ts` (unsupported embedding options / unknown tensor types).
  The preliminary staging snapshot did not include that upstream demo file; its
  initial successful root typecheck did not establish full-checkout type safety.
- Docker Compose configuration validated with example domain/data-root variables.
- The original API served a synthetic exported bundle: `/health`, `/api/stats`,
  `/api/tag-clusters`, hop-filtered `/api/relationships`, document text and the
  production frontend all returned 200. The expected single relationship and
  source text were asserted. The published database checksum remained unchanged
  after API startup and reads with the default read-only connection.
- Live FCC/NVIDIA, a full real DOJ dump, the upstream LFS binary, container
  build/runtime and public hosting were not established by those checks.
