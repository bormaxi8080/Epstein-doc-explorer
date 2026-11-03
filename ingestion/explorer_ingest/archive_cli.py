"""Standalone archive acquisition. Only the explicit fetch command makes HTTP requests."""

import argparse
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from ijson.common import JSONError
from jsonschema import ValidationError

from .archive_catalog import CONTROL, Archive, StorageUnavailable, artifact_json, plan, report
from .archive_transfer import Options, fetch, quarantine, storage_check, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--destination",
        required=True,
        type=Path,
        help="Existing external data directory; independent of JSON and evidence DB",
    )
    parser.add_argument(
        "--config", type=Path, help="Downloader limits/allowed-hosts JSON; no credentials"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    planning = commands.add_parser(
        "plan", help="Offline: validate and copy an immutable JSON inventory"
    )
    planning.add_argument("--dump", required=True, type=Path)
    planning.add_argument(
        "--evidence-db", type=Path, help="Optional closed PW-010 SQLite backup, opened read-only"
    )
    planning.add_argument("--allow-partial", action="store_true")
    downloading = commands.add_parser(
        "fetch", help="Explicit network download; never runs OCR or models"
    )
    downloading.add_argument(
        "--limit", type=int, default=0, help="Maximum pending assets; 0 means all"
    )
    downloading.add_argument("--allow-partial", action="store_true")
    checking = commands.add_parser(
        "verify", help="Offline: rehash, check formats and all placement references"
    )
    checking.add_argument(
        "--limit", type=int, default=0, help="Bounded audit; only a full audit can pass"
    )
    commands.add_parser("status", help="Offline ledger status; not a fresh disk audit")
    repair = commands.add_parser(
        "quarantine", help="Offline: preserve one suspect artifact and permit its retry"
    )
    repair.add_argument("asset_id")
    args = parser.parse_args(argv)
    checkout = Path(__file__).resolve().parents[2]
    destination = args.destination.resolve()
    if checkout == destination or checkout in destination.parents:
        parser.error("Downloaded data must be outside the source checkout")
    if getattr(args, "limit", 0) < 0:
        parser.error("--limit cannot be negative")
    archive = None
    try:
        settings = json.loads(args.config.read_text()) if args.config else {}
        options = Options(**settings)
        archive = Archive(args.destination, create=args.command == "plan")
        if args.command == "plan":
            if not archive.meta("plan_complete"):
                storage_check(archive, options, args.dump.stat().st_size * 3)
            result = plan(archive, args.dump, args.evidence_db, args.allow_partial)
        elif args.command == "fetch":
            with archive.db:
                archive.set_meta("last_fetch_options", asdict(options))
            result = fetch(archive, options, args.limit, args.allow_partial)
        elif args.command == "verify":
            result = verify(archive, options, args.limit)
        elif args.command == "quarantine":
            result = quarantine(archive, args.asset_id)
        else:
            result = report(archive)
        archive.check()
        artifact_json(
            archive.path(f"{CONTROL}/report.json"),
            "report",
            report(archive) if args.command == "quarantine" else result,
        )
        print(json.dumps(result, indent=2))
        if args.command in ("fetch", "verify"):
            if result["statuses"].get("failed"):
                return 1
            if (
                args.command == "verify"
                and not result["complete_at_last_audit"]
                or not result["download_complete"]
            ):
                return 2
        return 0
    except StorageUnavailable as exc:
        print(json.dumps({"status": "paused", "reason": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError, sqlite3.Error, ValidationError, JSONError) as exc:
        if (isinstance(exc, OSError) and exc.errno in (28, 122)) or (
            isinstance(exc, sqlite3.Error)
            and getattr(exc, "sqlite_errorcode", None) == getattr(sqlite3, "SQLITE_FULL", 13)
        ):
            print(
                json.dumps(
                    {
                        "status": "paused",
                        "reason": "External storage is full; durable data retained",
                    }
                )
            )
            return 2
        # Detailed records remain in the ledger; avoid echoing document metadata.
        print(
            json.dumps(
                {
                    "status": "error",
                    "type": type(exc).__name__,
                    "reason": str(exc)
                    if isinstance(exc, ValueError)
                    else "Invalid input, configuration, or local storage",
                }
            )
        )
        return 1
    finally:
        if archive is not None:
            archive.close()


if __name__ == "__main__":
    raise SystemExit(main())
