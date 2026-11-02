#!/usr/bin/env python3
"""Refresh this checkout's graph without hard-coded machine paths."""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fast", "moderate", "full"), default="fast")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    binary = shutil.which("codebase-memory-mcp")
    root = Path(__file__).resolve().parents[1]
    if not binary:
        print("WARNING: Codebase Memory MCP executable unavailable", file=sys.stderr)
        return int(args.strict)
    operation = "index_status" if args.status else "index_repository"
    payload = (
        {"project": "Epstein-doc-explorer"}
        if args.status
        else {
            "repo_path": str(root),
            "name": "Epstein-doc-explorer",
            "mode": args.mode,
            "persistence": False,
        }
    )
    try:
        result = subprocess.run(
            [binary, "cli", operation],
            input=json.dumps(payload),
            text=True,
            check=False,
            timeout=120,
        )
        return result.returncode if args.strict else 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"WARNING: Codebase Memory MCP refresh failed: {type(exc).__name__}", file=sys.stderr)
        return int(args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
