# Downloader, OCR, review and Explorer publication

Install the project as described in self-hosting.md, obtain the publisher-pinned
pipeline data profile, and run explorer-data prepare-workflow. Configs and
commands.json live outside Git. Each command below is an explicit operator step;
installation never starts a full archive download or inference.

## Original acquisition

Use the generated plan command with the complete JSON or parts manifest and
optional standalone source SQLite. The JSON is authoritative. Keep logical
layout to retain DOJ hierarchy. Paths for inputs, destination and OCR remain
independent. Start with the fixed pilot; fetch/verify --pilot is scoped separately
from full-source totals. Only fetch makes source requests. Normal browser session
cookies, if required, stay in a private Netscape cookie file referenced by local
config. No credentials enter release metadata.

Use generated pilot_select, pilot_fetch and pilot_verify commands in that order.
For an installed private pilot, import-pilot can adopt its existing originals
instead of fetching. A fresh scoped audit is still performed. Resume preserves
receipts/validated partials; HTML, source changes or bad content remain failures.
Full acquisition uses the separately generated full_fetch and full_verify.
Audit requires all selected files; a bounded diagnostic cannot certify them.

## OCR and analysis

Initialize the corpus once. Freeze a source selection only after its matching
fresh pilot/full audit, import selection, then plan corpus work. Frozen records
retain every source occurrence and binary SHA. Run bounded offline work first;
quality checks choose native text or queue image OCR. ZIP members retain lineage
and resource limits; DAT/OPT text is preserved without guessed record semantics.

Set FCC_API_KEY through your secret environment. Run the generated fcc_preflight
before inference: it checks the explicit route/catalogue and zero supported
prices. Then run bounded model_work with --allow-fcc. Only the explicit
private_model_work command also admits --allow-review, producing private drafts.
Caps, timing, prompt/schema/routing versions and response provenance are durable.
Changing data invalidates affected results. 429/transport ambiguity pauses and
retains state; inspect status and retry after the reported cooldown.

PDF/image/text/ZIP processing is implemented. Audio/video/Office remain visible
unsupported states until a compatible ASR/conversion contract is verified. The
current FCC Messages image/text contract is not an audio API. Source-byte, parser,
pixel and scratch limits also remain explicit gates, never silent omissions.

## Human verification and correction

Compare each source page with its transcript and evidence. Model review flags,
photos/blank pages, disagreements and missing layout text are separate signals.
An exact quote match is mechanical validation, not factual or human approval.
Review individual pages with an identified reviewer and reason:

```bash
explorer-corpus --config /external/configs/corpus.json review-page DOC_ID 1 --reviewer REVIEWER --reason 'Compared with the original page'
explorer-corpus --config /external/configs/corpus.json review-page DOC_ID 1 --reviewer REVIEWER --reason 'Corrected against original' --text-file /external/corrected-page.txt
```

The correction file is bounded UTF-8. Its SHA, reviewer/reason and previous page
SHA are retained; old OCR/layout remains origin evidence, not corrected geometry.
--no-body-text is an alternative explicit finding for a photo/blank page. Page
changes invalidate canonical text, analysis and approval. Run advance and bounded
work again, then inspect and approve the current document individually:

```bash
explorer-corpus --config /external/configs/corpus.json advance
explorer-corpus --config /external/configs/corpus.json work --limit 100 --allow-fcc
explorer-corpus --config /external/configs/corpus.json approve DOC_ID
```

There is no bulk approval shortcut. Aliases and semantic tag changes also require
reviewed reasons; redacted/unknown people remain document-scoped. Allegations,
testimony, denials and document statements stay distinct.

## Export and activate

Export a new bundle from the unchanged upstream baseline and the pinned cluster
dictionary. Use --base-db and --clusters as independent parameters. Public export
selects approved current documents; --include-unreviewed is an explicit private
review mode. TXT, full_text, pages, evidence and model provenance remain compatible.
Check SQLite integrity/FKs, all manifest hashes, TXT consistency and coverage.
Activate database/clusters/TXT as one version and restart Viewer; preserve the old
release for rollback. See distribution.md for portable release/installation tools.

The UI is a catalogue/search and bounded graph over processed documents. Complete
inventory, download, OCR, analysis and approval are separate coverage values. A
successful setup command, exhausted queue or HTTP health response is not full
archive completion. Status/work exit 3 can describe pending human review even
when all selected OCR and analyses are assembled.
