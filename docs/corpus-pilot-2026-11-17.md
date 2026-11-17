# Private pilot analysis completed — 2026-11-17

## Result and scope

The November 17 continuation (Asia/Almaty) completed the remaining 245 document
analyses from the frozen 250-PDF selection. All 250 files now have OCR, canonical
TXT and mechanically validated private draft analysis. No inference workers or
automatic retry loop remain running. FCC itself was left available.

This is the selected pilot, not the full archive. Public approvals remain zero;
250 pages still require quality review. `verification.json` reports
`ocr_complete=true` and `analysis_complete=true`; overall `complete=false` and
`full_archive_complete=false` correctly retain those review/scope gates. A CLI
exit code 3 therefore does not imply that automated analysis is still queued.

| Check | Verified result |
| --- | --- |
| Selected originals | 250 PDFs, 250 pages, 110,614,300 bytes |
| Original source hashes after processing | All 250 unchanged |
| OCR / canonical TXT / document analyses | 250 / 250 / 250 |
| Current analysis fragments, schema/date/exact-quote validation | 250 passed |
| TXT files versus SQLite `full_text` UTF-8 hashes | All 250 matched |
| Pending / running / retry / failed jobs | 0 / 0 / 0 / 0 |
| Historical job states | 1,039 done; 255 superseded with history |
| Pages requiring quality review / public approvals | 250 / 0 |
| Current prompt versions | 249 v3; one explicitly upgraded v4 |
| New draft relationships with page quotes | 15 |
| Bundle documents / active relationships | 25,482 / 107,036 |
| SQLite integrity / foreign keys | Passed / no violations |

## Provider failure and targeted repair

The route stayed
`claude-3-freecc-no-thinking/open_router/inclusionai/ling-3.2-flash:free`
through local FCC. Admission checked the catalogue's zero inference prices; no
paid fallback, account rotation or bulk archive operation was used. Requested and
reported model identities remain in the external artifacts; they do not provide
independent attestation of FCC's upstream backend.

One 2,496-character fragment produced two rejected v3 answers: 10 of 15 quotes
were absent as exact substrings of the OCR text. Generic correction did not
identify the invalid paths or include the previous answer. Prompt v4 adds exact
whitespace guidance and bounded diagnostic/previous-answer feedback. Synthetic
coverage verifies corrected newline quotes, strict rejection, the feedback byte
budget, legacy request compatibility and preserved unrelated approvals.

Only the rejected document was explicitly upgraded. All other 249 plan IDs and
all 180 already assembled analysis references were verified unchanged. The old
failed job and both rejected answers were retained as history. A v4 request hit
the 120-second transport bound; after a 45-second pause its queued retry returned
15 relationships with all quotes matching the source text. Application validation
did not normalize or silently substitute model quotes.

The continuation reserved 248 model calls: 247 have completed response envelopes;
one timed-out request retains an unknown upstream outcome. Its durable `started`
call-budget row records an attempted request, not a running worker. All local
jobs are finished. A matching quote is mechanical validation, not factual or
human approval of an asserted relationship.

## OCR quality limits

The model labelled 247 pages as photos, one as a table and two as mixed content.
These are unreviewed model labels. OCR transcriptions contain 7,201 characters
before canonical page markers. Review reasons overlap: 247 page-kind/body-coverage
checks, 51 model review requests, eight native/OCR disagreements and one page with
a block/transcript mismatch. No quality flag was automatically cleared.

This first Data Set 1 sample is unsuitable as a representative accuracy or
throughput estimate for the entire archive. Human page checks, current-version
approval, a representative acceptance sample and media-specific validation remain
separate gates. Audio/video/Office support remains as described in the
[workflow guide](corpus-pipeline.md).

## Current artifacts

| Artifact | Path |
| --- | --- |
| Latest private bundle | `/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261117-analysis-complete/` |
| Compatible SQLite | `document_analysis.db` in that bundle (369,377,280 bytes) |
| Canonical TXT | `data/ocr/<prefix>/<doc_id>.txt` in that bundle |
| Corpus and retained responses | `/Volumes/Transcend/data/EA-ocr/pilot-250/` |
| Latest coverage/hash verification | `/Volumes/Transcend/data/EA-ocr/pilot-250/verification.json` |
| Dated verification | `/Volumes/Transcend/data/EA-ocr/pilot-250/verification-20261117-analysis-complete.json` |
| Latest local API proof | `/Volumes/Transcend/data/EA-ocr/pilot-250/api-verification.json` |
| Targeted upgrade proof | `/Volumes/Transcend/data/EA-ocr/pilot-250/prompt-upgrade-verification-20261117.json` |
| Prior OCR-complete verification | `/Volumes/Transcend/data/EA-ocr/pilot-250/verification-20261116-ocr-complete.json` |

Published bundle directories are immutable. The previous November 16 bundles,
original PDF paths, downloader ledger, inventory parts and upstream database
remain in place. The [November 16 record](corpus-pilot-2026-11-16.md) is historical.
The root verification/API files are current pointers; dated reports preserve the
previous checks. The new manifest explicitly says `include_unreviewed=true` and
`originals_included=false`.

SHA-256 checks:

- New bundle SQLite: `5be7340d067846ac30a53ada0d1fd3e46b26b333bf4d11a4ce8fe8d4b35b755e`.
- New cluster dictionary: `603b8c321f0895bdca662ec743f9bc766c33f09b6f15e86de300b44ad039a979`.
- Unchanged upstream baseline: `a26cfa2d0daa17d32c0d884b6e13062009a9d473d96268c3cd8a77136b0c7c26`.
- Unchanged original dump: `7e4791264d49dd256c1eaaf381e5da0764d2bc4d39f7fea56614abce740a47b8`
  (1,653,169,917 bytes).

All baseline rows remain active or in explicit issue tables: 139 cyclic/feed-in
aliases and nine orphan RDF rows retain their complete source rows in quarantine.
The baseline was not edited.

## Verification and local inspection

All **155 offline tests passed** with loopback fixtures and external Python
sockets blocked. Ruff and `git diff --check` passed. Real bundle verification
rehashes originals, validates every current analysis against its page text,
compares all TXT/SQLite hashes, and checks integrity/foreign keys/journal mode.

The local API returned all 250 new catalogue rows with `pending` review state,
OCR pages with `needs_review`, statistics, relationships and actor counts. It also
returned all 15 repaired-document evidence rows with exact source quotes. The
SQLite checksum stayed unchanged and no WAL/SHM sidecars were created. Temporary
API processes were stopped. SQLite scratch stayed on the external disk.

From the Explorer checkout, inspect status or start the private local API:

```bash
.venv/bin/python scripts/process_corpus.py --config examples/corpus-pilot.json status
HOST=127.0.0.1 SQLITE_TMPDIR=/Volumes/Transcend/data/EA-explorer/api-tmp \
DB_PATH=/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261117-analysis-complete/document_analysis.db \
TAG_CLUSTERS_PATH=/Volumes/Transcend/data/EA-explorer/releases/pilot-250-private-20261117-analysis-complete/tag_clusters.json npm run api
```

No additional inference is needed to complete the automated pilot. Review or
source changes may invalidate affected downstream versions; inspect status after
such changes. No public release, push or full-archive processing was performed.
