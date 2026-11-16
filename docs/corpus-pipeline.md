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
| 2 | Real upstream baseline and compatibility checks | Pending |
| 3 | Verified pilot/full source selection | Pending |
| 4 | Durable leased job queue | Pending |
| 5 | External storage and resource limits | Pending |
| 6 | File/document/page registry | Pending |
| 7 | Native extraction and isolated rendering | Pending |
| 8 | Multimodal FCC transport | Pending |
| 9 | OCR, quality checks and pilot | Pending |
| 10 | Canonical text and page maps | Pending |
| 11 | Chunk analysis and document assembly | Pending |
| 12 | Tags, entities and graph | Pending |
| 13 | Compatible export, API and UI | Pending |
| 14 | Other formats and scale validation | Pending |
| 15 | Bounded full-corpus operation | Pending |

Every implemented stage has regression tests and an Unreleased changelog entry.
Network/model operations are explicit; offline tests use synthetic loopback
services. Public export still requires document approval. No full-corpus
completion can be claimed while sources, pages or required stages are pending.
