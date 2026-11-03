#!/usr/bin/env python3
"""Create a labeled synthetic corpus outside the source checkout; no downloads."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
from explorer_ingest.synthetic_corpus import generate_corpus  # noqa: E402

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    target = args.output.resolve()
    if root == target or root in target.parents:
        parser.error("Synthetic data must be outside the checkout")
    print(json.dumps(generate_corpus(target), indent=2))
