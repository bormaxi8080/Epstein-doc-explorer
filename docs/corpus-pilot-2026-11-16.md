# Corpus implementation and real pilot — 2026-11-16

## Result and scope

The core staged workflow is implemented with separate stage commits, regression
tests and Unreleased changelog entries. PDF/image/text processing, bounded ZIP
members, durable jobs, free FCC admission, canonical TXT, evidence analysis,
reviewed tags/aliases, compatible export and catalogue are available. Continuous
work advances downstream jobs without one invocation per document.

The real pilot is **incomplete**. The free route returned HTTP 429 after 215 of
250 PDF pages. Its reset time and whether the limit is daily or transient were
not established. All inference processes were stopped; no paid fallback,
provider/account rotation or automatic later retry was scheduled.

| Check | Observed result |
| --- | --- |
| Frozen originals | 250 PDF files, 250 pages, 110,614,300 bytes |
| Originals rehashed after processing | All 250 unchanged |
| OCR responses retained | 215 pages |
| Remaining OCR | 35 pages, retained in pending/retry jobs |
| Canonical TXT | 215 private drafts |
| Pages requiring quality review | 215; no automatic photo/blank approval |
| Accepted real analysis | 0 |
| Public approvals | 0 |
| TXT versus SQLite UTF-8 hashes | All 215 matched |
| SQLite integrity / foreign keys | Passed / zero issues in new bundle |
| Original baseline | Unchanged SHA-256 below |

FCC 5.14.7 was exercised through the explicitly authorized route
`claude-3-freecc-no-thinking/open_router/inclusionai/ling-3.2-flash:free`.
The model catalogue advertised zero prompt/completion prices and image input.
A synthetic image smoke matched its expected text. These checks do not establish
OCR accuracy on unreviewed archive pages or independently attest the upstream
backend behind FCC's reported model string.

The draft analysis check exposed a transport timeout, an extra response property
and a quote absent from the supplied fragment. They were rejected. Prompt v2 and
one budgeted correction are regression-tested; their live acceptance remains
pending because further inference was stopped on HTTP 429. Old failures remain
as superseded history with replacement jobs. No unsupported relationship was
published as accepted analysis.

## Current external artifacts

| Artifact | Path / purpose |
| --- | --- |
| Frozen handoff | `/Volumes/Transcend/data/EA-ocr/selections/pilot-250/selection.json` |
| Corpus ledger/artifacts/TXT | `/Volumes/Transcend/data/EA-ocr/pilot-250/` |
| Latest verification | `/Volumes/Transcend/data/EA-ocr/pilot-250/verification.json` |
| Latest private bundle | `/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261116-partial-v2/` |
| Bundle database | `document_analysis.db` inside that bundle |
| Compatible TXT | `data/ocr/<prefix>/<doc_id>.txt` inside that bundle |
| Earlier private snapshot | `pilot-250-private-20261116-partial/`; retained unchanged |
| Verified upstream baseline | `/Volumes/Transcend/data/EA-explorer/baseline/document_analysis.db` |

The bundle contains 25,447 document rows: 25,232 upstream documents and 215 new
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

The complete offline suite blocks external Python sockets and uses local archive,
FCC and API fixtures. Regression coverage includes changed-page/model cache
invalidation, adaptive fragments, bounded corrections, strict evidence rejection,
leases/recovery, nested ZIP provenance, resource limits, reviewed mappings and a
100,002-relationship API fixture beyond the former pre-filter cap. API typecheck
and frontend production build passed. A real browser verified zero-edge document
catalogue access, page navigation and `needs_review` rendering; its same-origin
CORS failure was fixed. Temporary UI servers were stopped.

The million-job synthetic queue benchmark scheduled in 13.45 seconds, resolved
1,000 jobs in 1.53 seconds and peaked at 39,043,072 RSS bytes. Before indexed claim
selection those resolutions took 67.76 seconds. This measures queue persistence,
not model throughput. Benchmark databases were removed; JSON measurements remain
under `/Volumes/Transcend/data/EA-ocr/benchmarks/`.

## Resume and remaining gates

After the provider permits calls, configure the FCC proxy token through
`FCC_API_KEY` in the environment, then run from the Explorer checkout:

```bash
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json status
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json work --stage ocr --limit 50 --allow-fcc --allow-review
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json work --stage text --limit 50 --allow-review
```

Status/work exit 3 means incomplete or paused. Use the stage-specific OCR command
to finish the remaining transcription before starting analysis. Private analysis
can then run through `work --stage analyze --allow-fcc --allow-review`; synthesis
uses stage `assemble`. Default public approval still requires reviewed pages and
current validated analysis. Do not treat successful HTTP or a zero process exit
from an individual setup command as complete archive processing.

Audio/video/Office conversion needs a verified compatible FCC ASR/conversion
contract and remains explicitly unsupported. A representative 5,000-file quality
acceptance run and real storage/throughput projections are still gates before a
full archive run. Only the existing 250 originals were used; no bulk download,
public release or push was performed.
