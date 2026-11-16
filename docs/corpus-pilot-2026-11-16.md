# Corpus implementation and real pilot — 2026-11-16

## Result and scope

The core staged workflow is implemented with separate stage commits, regression
tests and Unreleased changelog entries. PDF/image/text processing, bounded ZIP
members, durable jobs, free FCC admission, canonical TXT, evidence analysis,
reviewed tags/aliases, compatible export and catalogue are available. Continuous
work advances downstream jobs without one invocation per document.

**OCR coverage of the selected 250-file pilot is complete.** Analysis and human
quality review are separate incomplete stages. HTTP 429 initially stopped OCR at
215 pages; a later bounded retry succeeded through the same free route, and the
remaining pages completed. No paid fallback or provider/account rotation was used.
All inference workers have stopped after the bounded checks; no later automatic
retry or bulk archive operation was scheduled. The session began November 16 and
finished November 17 in Asia/Almaty; artifact names retain the original run date.

| Check | Observed result |
| --- | --- |
| Frozen originals | 250 PDF files, 250 pages, 110,614,300 bytes |
| Originals rehashed after processing | All 250 unchanged |
| OCR responses retained | 250 pages |
| Remaining OCR | 0 |
| Canonical TXT | 250 private drafts |
| Pages requiring quality review | 250; no automatic quality approval |
| Validated real draft analysis | 5 documents; another 245 remain queued |
| Public approvals | 0 |
| TXT versus SQLite UTF-8 hashes | All 250 matched |
| SQLite integrity / foreign keys | Passed / zero issues in new bundle |
| Original baseline | Unchanged SHA-256 below |

FCC 5.14.7 was exercised through the explicitly authorized route
`claude-3-freecc-no-thinking/open_router/inclusionai/ling-3.2-flash:free`.
The model catalogue advertised zero prompt/completion prices and image input.
A synthetic image smoke matched its expected text. These checks do not establish
OCR accuracy on unreviewed archive pages or independently attest the upstream
backend behind FCC's reported model string.

Initial draft checks exposed a transport timeout, extra schema properties and
an unsupported evidence quote. They were rejected and retained as history.
Prompt v3 requests data instances instead of schema declarations; its real window
completed **5 of 5 attempted jobs**, followed by five assembled document analyses.
Schema, date and exact-quote validators were retained. These are private drafts,
not factual or human approval. The successful sample added no relationship edges;
its documents remain visible through the independent catalogue.

The v2 trial revealed a work-limit bug: failures were omitted from the limit,
causing nine jobs to be attempted instead of the intended five. The limit now
counts every claimed job, including failures, with regression coverage. Obsolete
prompt jobs retire with history rather than being silently deleted.

## Current external artifacts

| Artifact | Path / purpose |
| --- | --- |
| Frozen handoff | `/Volumes/Transcend/data/EA-ocr/selections/pilot-250/selection.json` |
| Corpus ledger/artifacts/TXT | `/Volumes/Transcend/data/EA-ocr/pilot-250/` |
| Latest verification | `/Volumes/Transcend/data/EA-ocr/pilot-250/verification.json` |
| Latest private bundle | `/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261116-final/` |
| Bundle database | `document_analysis.db` inside that bundle |
| Compatible TXT | `data/ocr/<prefix>/<doc_id>.txt` inside that bundle |
| Earlier private snapshots | Names ending `partial`, `partial-v2`, and `ocr-complete`; retained unchanged |
| Verified upstream baseline | `/Volumes/Transcend/data/EA-explorer/baseline/document_analysis.db` |

The bundle contains 25,482 document rows: 25,232 upstream documents and 250 new
drafts. Active RDF rows number 107,021. Nine original upstream RDF rows reference
absent documents; their complete rows are retained in
`ingestion_baseline_triple_issues`, without fabricated documents. Another 139
upstream alias rows lie in or lead into cycles; full rows are retained in
`ingestion_alias_issues`, with those identities unresolved. The manifest reports
both counts and corpus coverage. Baseline SHA-256 remains
`a26cfa2d0daa17d32c0d884b6e13062009a9d473d96268c3cd8a77136b0c7c26`.

The original dump, inventory parts, downloader ledger and original PDF paths were
not moved or modified. The real inventory has 1,396,719 unique linked assets;
neither that count nor its 27,980 web pages establishes PDF page count.

## Verification

The complete offline suite contains 151 passing tests and blocks external Python sockets and uses local archive,
FCC and API fixtures. Regression coverage includes changed-page/model cache
invalidation, adaptive fragments, bounded corrections, strict evidence rejection,
leases/recovery, nested ZIP provenance, resource limits, reviewed mappings and a
100,002-relationship API fixture beyond the former pre-filter cap. API typecheck
and frontend production build passed. The final real bundle also passed local
API stats/catalogue/relationship/actor-count requests, with its database checksum
unchanged. Published SQLite uses DELETE mode and requires no WAL/SHM sidecars.
API SQLite scratch defaults to bounded memory, or an explicitly validated
external SQLITE_TMPDIR. A real browser verified zero-edge document
catalogue access, page navigation and `needs_review` rendering; its same-origin
CORS failure was fixed. Temporary UI servers were stopped.

The million-job synthetic queue benchmark scheduled in 13.45 seconds, resolved
1,000 jobs in 1.53 seconds and peaked at 39,043,072 RSS bytes. Before indexed claim
selection those resolutions took 67.76 seconds. This measures queue persistence,
not model throughput. Benchmark databases were removed; JSON measurements remain
under `/Volumes/Transcend/data/EA-ocr/benchmarks/`.

## Resume and remaining gates

Set the FCC proxy token through `FCC_API_KEY` in the environment. From the
Explorer checkout, inspect status or continue the remaining private analysis:

```bash
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json status
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json work --stage analyze --limit 25 --allow-fcc --allow-review
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json work --stage assemble --limit 25 --allow-fcc --allow-review
```

Status/work exit 3 means the complete selected-corpus workflow remains unfinished;
it does not mean that the OCR stage failed. Current metrics are 250 texts,
zero missing pages, five analyses, 245 pending analyses, 250 pages requiring review
and zero approvals. `verification.json` additionally records `ocr_complete=true`.
A successful setup command or an individual HTTP request is not archive completion.

For local inspection of the private bundle, use the existing external scratch
directory and bind only to loopback:

```bash
HOST=127.0.0.1 SQLITE_TMPDIR=/Volumes/Transcend/data/EA-explorer/api-tmp \
DB_PATH=/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261116-final/document_analysis.db \
TAG_CLUSTERS_PATH=/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261116-final/tag_clusters.json npm run api
```

Public approval still requires reviewed pages and current validated analysis.
Full-corpus completion remains false; this is a frozen pilot selection.

Audio/video/Office conversion needs a verified compatible FCC ASR/conversion
contract and remains explicitly unsupported. A representative 5,000-file quality
acceptance run and real storage/throughput projections are still gates before a
full archive run. Only the existing 250 originals were used; no bulk download,
public release or push was performed.
