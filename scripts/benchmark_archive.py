#!/usr/bin/env python3
"""Generate/plan synthetic inventories; never fetch fixture URLs or call models."""

import argparse
import json
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ingestion"))
import explorer_ingest.archive_catalog as planner  # noqa: E402
from explorer_ingest.archive_catalog import Archive, plan  # noqa: E402
from explorer_ingest.state import digest_file  # noqa: E402
from explorer_ingest.synthetic_archive import generate_inventory  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()))
    parser.add_argument("--reserve-bytes", type=int, default=8 * 1024**3)
    parser.add_argument("--max-rss-mb", type=int, default=512)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.count < 1 or not args.scratch.is_dir() or args.reserve_bytes < 0:
        parser.error("Positive count, existing scratch directory and nonnegative reserve required")
    estimate = args.count * 5000
    if shutil.disk_usage(args.scratch).free < args.reserve_bytes + estimate:
        parser.error("Insufficient scratch space above the configured reserve")
    with tempfile.TemporaryDirectory(prefix="explorer-scale-", dir=args.scratch) as temporary:
        root = Path(temporary)
        source = root / "synthetic.json"
        started = time.monotonic()
        generated = generate_inventory(source, args.count)
        generation_seconds = time.monotonic() - started
        destination = root / "archive"
        destination.mkdir()
        archive = Archive(destination, create=True)
        original_check = archive.check

        def checked_storage():
            original_check()
            if shutil.disk_usage(args.scratch).free < args.reserve_bytes:
                raise ValueError("Scratch reserve reached; benchmark stopped")

        archive.check = checked_storage
        try:
            started = time.monotonic()
            result = plan(archive, source)
            seconds = time.monotonic() - started
            assert result["total"] == args.count and not result["download_complete"]
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            rss_bytes = rss if sys.platform == "darwin" else rss * 1024
            used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
            result = {
                **generated,
                "generation_seconds": generation_seconds,
                "planning_seconds": seconds,
                "peak_rss_bytes": rss_bytes,
                "scratch_bytes": used,
                "database_bytes": (archive.control / "archive.sqlite3").stat().st_size,
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "network_requests": 0,
                "planner_sha256": digest_file(Path(planner.__file__)),
            }
            if rss_bytes > args.max_rss_mb * 1024**2:
                raise ValueError("Planner peak RSS exceeded the configured acceptance threshold")
            if args.output:
                args.output.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        finally:
            archive.close()


if __name__ == "__main__":
    main()
