# Distribution acceptance — 17 November 2026

Application 1.2.0, runtime source commit `1a901b45fcc3c28d9379166e0af990ed3ddb59b3`.
Data payloads remain external; Git contains contracts, code, guides, synthetic
fixtures, this report, the public descriptor and publisher-pinned checksums.
At initial packaging, Drive URLs were unset. The operator subsequently supplied
the public folder; the current [Drive verification record](../releases/ea-inventory-20261117/drive-verification.json)
records separate cloud checks without changing the local acceptance results.

| Release | Visibility | Artifacts | Transport bytes | All decoded bytes |
| --- | --- | ---: | ---: | ---: |
| ea-inventory-20261117 | Public | 33 | 1,637,599,778 | 8,475,930,978 |
| ea-pilot-250-private-20261117 | Private review | 509 | 821,325,792 | 3,134,618,571 |

Public descriptor SHA-256: `460936f4a3b50945128d6cbc1b6a6b8384a49e26dc6e17abccab34dd7cada8e9`.
Private descriptor SHA-256: `ec662a33bf4c22885eb8fa5b911274350b5c50b776c655413bbd968fa8f13c1c`.

Prepared upload folders:

- `/Volumes/Transcend/data/EA-distribution/public/`
- `/Volumes/Transcend/data/EA-distribution/private-review/` (separate, unapproved)

Public contents include the exact full inventory, 24 independently compressed
parts plus their original manifest, the paired frozen complete producer SQLite,
pristine upstream baseline, clean baseline-only Viewer, full SQLite/FTS catalogue
and portable source provenance. The stale checkpoint pointer was not used.
Private contents include 250 unchanged PDFs under logical hierarchy, original
receipts in source rows, page OCR/layout, canonical TXT, chunk analyses/provenance,
the private Viewer and the same full catalogue. No working queue, secrets, cookies
or raw HTTP envelopes are distributed. Source maps are separate from payloads.

## Verified results

- 186 offline regression tests passed; API typecheck, Ruff and production UI build passed.
- Browser Chrome acceptance: synthetic folder navigation, source details, filename
  search, explicit missing-text state, escaped untrusted markup and desktop/mobile screenshots.
- Linux ARM64 Docker Viewer and offline processor passed with digest-pinned bases,
  512 MiB container limits, mounted read-only Viewer data and no processor network.
  Native checks used macOS ARM64. Linux AMD64 CI is configured but not claimed run here.
- Real whole/parts semantic signatures match; standalone database integrity,
  snapshot/count binding, TXT hashes and review gates passed.
- Actual complete public profile and all private artifacts were imported into a
  new external workspace, fully verified and renamed. The private installation was
  activated using a relative pointer; public switch/rollback is covered by regression.
- Relocated APIs expose all 1,396,719 inventory assets. Public Viewer: 25,232 existing
  documents, no pending imported rows. Private Viewer: 25,482 documents, 250 pending.
  Three unchanged upstream records lack text and remain explicit, rather than
  being counted as available transcriptions.
- All 250 real source-to-document mappings and all 15 private evidence quotes were
  checked through the relocated API. These checks are mechanical, not human approval.
- Original full JSON SHA-256 remains
  `7e4791264d49dd256c1eaaf381e5da0764d2bc4d39f7fea56614abce740a47b8`;
  baseline SHA-256 remains
  `a26cfa2d0daa17d32c0d884b6e13062009a9d473d96268c3cd8a77136b0c7c26`.
  All 250 origin binaries still match frozen hashes.
- Catalogue construction verified all parts with bounded memory (about 203 MiB peak
  footprint); JSON, SQLite, compressed payloads, verification scratch and TAR
  outputs remained external. Transient Docker build layers used guarded system
  storage; free-space safety floors held; only test-owned containers/tags/builders were removed.
- No additional DOJ downloads, FCC/model calls, publication, Google upload, Git push
  or tag creation occurred during packaging.

Detailed local proofs: `/Volumes/Transcend/data/EA-distribution/work/real-distribution-verification.json`,
`containers-a9e4f6f0a1c2/verification.json`, `browser-iXnbWj/verification.json`,
release verification.json files and catalogue build log under the same work root.

## Remaining external acceptance

The public Drive folder and all 33 payload names are visible anonymously, and
the downloaded small manifest matches the publisher pin. Complete cloud payload
hashes and authenticated API/quota/resource-key behavior remain unverified. TLS/domain acceptance awaits a deployment hostname. Neither
is inferred from loopback fixtures. The 250-file pilot is not representative of
all archive formats; audio/video/Office adapters and full-source processing/review
remain explicit scale gates documented in scale-operations.md. Fresh corpus work
on another machine creates local jobs; ready private text can be viewed without
inference, and raw page/analysis metadata remains portable for inspection.
