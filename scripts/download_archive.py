#!/usr/bin/env python3
"""Repository entry point for the separate PW-010 archive downloader."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))

from explorer_ingest.archive_cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
