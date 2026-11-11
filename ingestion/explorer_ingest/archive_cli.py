"""Standalone archive acquisition. Only the explicit fetch command makes HTTP requests."""

import argparse
import json
import os
import signal
import sqlite3
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path

from ijson.common import JSONError
from jsonschema import ValidationError

from .archive_catalog import CONTROL, Archive, StorageUnavailable, artifact_json, plan, report
from .archive_maintenance import backup, rebuild, restore
from .archive_observer import ArchiveView, doctor, snapshot, watch
from .archive_selection import create_pilot
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
    planning.add_argument("--layout", choices=("url", "logical"), default=None)
    selection = commands.add_parser(
        "pilot", help="Offline: freeze the first unique files in tree order"
    )
    selection.add_argument("--first", required=True, type=int)
    downloading = commands.add_parser(
        "fetch", help="Explicit network download; never runs OCR or models"
    )
    downloading.add_argument(
        "--limit", type=int, default=0, help="Maximum pending assets; 0 means all"
    )
    downloading.add_argument("--allow-partial", action="store_true")
    downloading.add_argument(
        "--pilot", action="store_true", help="Only the frozen pilot membership"
    )
    checking = commands.add_parser(
        "verify", help="Offline: rehash, check formats and all placement references"
    )
    checking.add_argument(
        "--pilot", action="store_true", help="Audit only the frozen pilot membership"
    )
    checking.add_argument(
        "--limit", type=int, default=0, help="Bounded audit; only a full audit can pass"
    )
    status = commands.add_parser("status", help="Read-only status; not a fresh disk audit")
    status.add_argument(
        "--watch", action="store_true", help="Print JSONL snapshots until interrupted"
    )
    status.add_argument("--interval", type=float, default=2)
    status.add_argument("--samples", type=int, default=0, help="0 watches until interrupted")
    diagnosis = commands.add_parser("doctor", help="Local preflight; no network")
    diagnosis.add_argument(
        "--write-probe", action="store_true", help="Explicit temporary fsync/rename probe"
    )
    saving = commands.add_parser("backup", help="Snapshot metadata; excludes original payloads")
    saving.add_argument("output", type=Path)
    restoring = commands.add_parser(
        "restore", help="Restore metadata into a destination with no plan"
    )
    restoring.add_argument("source", type=Path)
    rebuilding = commands.add_parser(
        "rebuild", help="Rebuild a missing ledger from JSON and receipts"
    )
    rebuilding.add_argument("--dump", required=True, type=Path)
    rebuilding.add_argument("--receipts", required=True, type=Path)
    rebuilding.add_argument("--allow-partial", action="store_true")
    rebuilding.add_argument("--layout", choices=("url", "logical"), default=None)
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
    old_term = None
    old_int = None
    stop_requested = False
    scratch_environment = None
    if threading.current_thread() is threading.main_thread():
        old_term = signal.getsignal(signal.SIGTERM)
        old_int = signal.getsignal(signal.SIGINT)

        def stop(signum, frame):
            nonlocal stop_requested
            stop_requested = True
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    try:
        settings = json.loads(args.config.read_text()) if args.config else {}
        options = Options(**settings)
        if args.command == "doctor":
            result = doctor(args.destination, options.reserve_bytes, args.write_probe)
            print(json.dumps(result, indent=2))
            return 0 if result["ok"] else 1
        if args.command == "restore":
            doctor(args.destination, options.reserve_bytes)
            result = restore(args.destination, args.source, options.reserve_bytes)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "status":
            archive = ArchiveView(args.destination)
            archive.db.set_progress_handler(lambda: 0, 1000)
            if args.watch:
                for result in watch(archive, args.interval, args.samples):
                    print(json.dumps(result), flush=True)
            else:
                print(json.dumps(snapshot(archive)[0], indent=2))
            return 0
        if args.command == "backup":
            output = args.output.resolve()
            if output == checkout or checkout in output.parents:
                raise ValueError("Backup metadata must be outside the source checkout")
        archive = Archive(
            args.destination,
            create=args.command in ("plan", "rebuild"),
            recover=args.command == "rebuild",
        )
        archive.db.set_progress_handler(lambda: 0, 1000)
        scratch_environment = (
            tempfile.tempdir,
            {key: os.environ.get(key) for key in ("TMPDIR", "SQLITE_TMPDIR")},
        )
        scratch = archive.path(f"{CONTROL}/tmp")
        archive.make_parent(scratch / "scratch")
        os.environ["SQLITE_TMPDIR"] = str(scratch)
        os.environ["TMPDIR"] = str(scratch)
        tempfile.tempdir = str(scratch)
        if args.command == "plan":
            if not archive.meta("plan_complete"):
                storage_check(archive, options, args.dump.stat().st_size * 3)
            result = plan(archive, args.dump, args.evidence_db, args.allow_partial, args.layout)
        elif args.command == "pilot":
            result = create_pilot(archive, args.first)
        elif args.command == "backup":
            result = backup(archive, args.output, options.reserve_bytes)
            print(json.dumps(result, indent=2))
            return 0
        elif args.command == "rebuild":
            storage_check(archive, options, args.dump.stat().st_size * 3)
            result = rebuild(
                archive, args.dump, args.receipts, options, args.allow_partial, layout=args.layout
            )
        elif args.command == "fetch":
            with archive.db:
                archive.set_meta("last_fetch_options", asdict(options))
            result = fetch(archive, options, args.limit, args.allow_partial, pilot=args.pilot)
        elif args.command == "verify":
            result = verify(archive, options, args.limit, pilot=args.pilot)
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
        if args.command in ("fetch", "verify") and args.pilot:
            selection_result = result["pilot"]
            if selection_result["statuses"].get("failed"):
                return 1
            succeeded = (
                selection_result["verified"]
                if args.command == "verify"
                else selection_result["download_complete"]
            )
            return 0 if succeeded else 2
        if args.command in ("fetch", "verify", "rebuild"):
            if result["statuses"].get("failed"):
                return 1
            if (
                args.command in ("verify", "rebuild")
                and not result["complete_at_last_audit"]
                or not result["download_complete"]
            ):
                return 2
        return 0
    except KeyboardInterrupt:
        print(
            json.dumps({"status": "paused", "reason": "Operator stop; durable checkpoint retained"})
        )
        return 2
    except StorageUnavailable as exc:
        print(json.dumps({"status": "paused", "reason": str(exc)}))
        return 2
    except (OSError, ValueError, TypeError, sqlite3.Error, ValidationError, JSONError) as exc:
        if stop_requested:
            print(
                json.dumps(
                    {"status": "paused", "reason": "Operator stop; durable checkpoint retained"}
                )
            )
            return 2
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
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
        if old_int is not None:
            signal.signal(signal.SIGINT, old_int)
        if scratch_environment is not None:
            tempfile.tempdir, previous_environment = scratch_environment
            for key, value in previous_environment.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        if archive is not None:
            archive.close()


if __name__ == "__main__":
    raise SystemExit(main())
