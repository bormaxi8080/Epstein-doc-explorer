"""A small clearly synthetic Viewer bundle; never calls a model or downloader."""

import hashlib
import json
import tempfile
from pathlib import Path

from .corpus_analysis import analyze_fragment, approve, assemble_analysis, plan_analysis
from .corpus_contracts import document_id
from .corpus_export import export_corpus
from .corpus_ocr import select_page
from .corpus_registry import Corpus
from .corpus_storage import CorpusLimits
from .corpus_text import assemble_text
from .distribution_contracts import ExternalDirectory


class SyntheticProvider:
    def identity(self):
        return {"model": "synthetic-no-network", "routing_revision": "demo-1"}

    def generate(self, prompt):
        return dict(
            output=dict(
                one_sentence_summary="SYNTHETIC demonstration document",
                paragraph_summary="SYNTHETIC local test data. No real source or model was used.",
                category="other",
                content_tags=["synthetic"],
                date_range_earliest=None,
                date_range_latest=None,
                rdf_triples=[],
            ),
            reported_model="synthetic-no-network",
            usage={},
        )


def demo_bundle(output, reserve_bytes=1024**3):
    output = Path(output).absolute()
    storage = ExternalDirectory(output.parent, reserve_bytes)
    if output.exists() or output.is_symlink():
        raise ValueError("Demo output must be a new directory")
    with tempfile.TemporaryDirectory(prefix=".demo-", dir=output.parent) as temporary:
        root = Path(temporary)
        corpus_root = root / "corpus"
        corpus_root.mkdir()
        corpus = Corpus(corpus_root, create=True, limits=CorpusLimits(reserve_bytes=reserve_bytes))
        try:
            text = (
                "SYNTHETIC demonstration text. This document contains no real archive information."
            )
            sha = hashlib.sha256(text.encode()).hexdigest()
            doc = document_id(sha)
            corpus.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,?)",
                (doc, sha, len(text.encode())),
            )
            corpus.inspected(doc, dict(format="text", status="inspected", pages=1))
            select_page(corpus, doc, 1, text, "native", {"synthetic": True})
            assemble_text(corpus, doc)
            client = SyntheticProvider()
            plan_analysis(corpus, doc, client.identity())
            job = corpus.queue.claim("synthetic-demo", "analyze")
            result = analyze_fragment(corpus, json.loads(job["payload"]), client)
            corpus.queue.finish(job["id"], job["token"], result)
            assemble_analysis(corpus, doc, client)
            approve(corpus, doc)
            clusters = root / "clusters.json"
            clusters.write_text(
                json.dumps([dict(id=0, name="Synthetic", tags=["synthetic"], exemplars=[])])
            )
            storage.check()
            export_corpus(corpus, output, clusters)
        finally:
            corpus.close()
    return dict(output=str(output), synthetic=True, documents=1, network_or_model_calls=False)
