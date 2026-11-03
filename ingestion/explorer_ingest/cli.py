"""Explicit commands: importing never downloads or calls a model implicitly."""

import argparse
import json
import os
from pathlib import Path

from .analysis import analyze_documents, approve_document
from .archive_adoption import adopt_archive
from .download import download_assets
from .export import export_bundle
from .extraction import extract_assets
from .inventory import import_inventory
from .provider import FCCProvider
from .state import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("import-inventory")
    inventory.add_argument("source", type=Path)
    inventory.add_argument("--allow-partial", action="store_true")
    adopt = sub.add_parser("adopt-archive")
    adopt.add_argument("archive_root", type=Path)
    download = sub.add_parser("download")
    download.add_argument("--allow-host", action="append", default=None)
    download.add_argument("--max-bytes", type=int, default=1024**3)
    for name in ("extract", "analyze"):
        sub.add_parser(name)
    sub.choices["extract"].add_argument("--max-bytes", type=int, default=512 * 1024**2)
    sub.choices["extract"].add_argument("--max-members", type=int, default=10000)
    for name in ("download", "extract", "analyze"):
        sub.choices[name].add_argument("--limit", type=int, default=10)
    sub.choices["analyze"].add_argument("--max-chars", type=int, default=16000)
    review = sub.add_parser("approve")
    review.add_argument("doc_id")
    export = sub.add_parser("export")
    export.add_argument("output", type=Path)
    export.add_argument("--clusters", required=True, type=Path)
    export.add_argument("--base-db", type=Path)
    export.add_argument("--aliases", type=Path)
    export.add_argument("--include-unreviewed", action="store_true")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    checkout = Path(__file__).resolve().parents[2]
    root = args.data_root.resolve()
    if root == checkout or checkout in root.parents:
        parser.error("--data-root must be outside the source checkout")
    if args.command == "export":
        destination = args.output.resolve()
        if destination == checkout or checkout in destination.parents:
            parser.error("Export bundles must be outside the source checkout")
    if getattr(args, "limit", 1) < 1 or getattr(args, "max_bytes", 1) < 1:
        parser.error("Limits must be positive")
    store = Store(root)
    try:
        with store.writer():
            if args.command == "import-inventory":
                result = import_inventory(store, args.source, args.allow_partial)
            elif args.command == "adopt-archive":
                result = adopt_archive(store, args.archive_root)
            elif args.command == "download":
                result = download_assets(
                    store, args.limit, args.max_bytes, allow_hosts=args.allow_host
                )
            elif args.command == "extract":
                result = extract_assets(store, args.limit, args.max_bytes, args.max_members)
            elif args.command == "analyze":
                provider = FCCProvider(
                    os.environ.get("FCC_BASE_URL", "http://127.0.0.1:8082"),
                    os.environ.get("FCC_MODEL", ""),
                    os.environ.get("FCC_ROUTING_REVISION", ""),
                    os.environ.get("FCC_API_KEY", ""),
                )
                result = analyze_documents(store, provider, args.limit, args.max_chars)
            elif args.command == "approve":
                result = approve_document(store, args.doc_id)
            elif args.command == "export":
                result = export_bundle(
                    store,
                    args.output,
                    args.clusters,
                    args.base_db,
                    args.include_unreviewed,
                    args.aliases,
                )
            else:
                result = store.status()
        print(json.dumps(result, indent=2))
        return (
            1
            if isinstance(result, list)
            and any(x.get("status", "").endswith("failed") for x in result)
            else 0
        )
    except (ValueError, OSError) as exc:
        # Avoid logging opaque provider errors or raw response bodies.
        parser.exit(1, f"Ingestion failed: {exc}\n")
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
