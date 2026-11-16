#!/usr/bin/env python3
"""Run the explicitly configured corpus workflow from an installed checkout."""

from explorer_ingest.corpus_cli import entry

if __name__ == "__main__":
    raise SystemExit(entry())
