"""Explicit distribution operations; importing this module never starts work."""

import argparse
import json

from jsonschema import ValidationError

from .distribution_contracts import PROFILES, load_workspace
from .distribution_demo import demo_bundle
from .distribution_doctor import diagnose
from .distribution_fetch import Paused, fetch_release
from .distribution_install import import_release, verify_install
from .distribution_pilot import export_pilot
from .distribution_release import build_release, verify_release
from .distribution_workflow import prepare_workflow


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--workspace")
    doctor.add_argument("--require-docker", action="store_true")
    doctor.add_argument("--component", choices=("all", "viewer", "processor"), default="all")
    demo = sub.add_parser("demo")
    demo.add_argument("--output", required=True)
    demo.add_argument("--reserve-bytes", type=int, default=1024**3)
    init = sub.add_parser("init")
    init.add_argument("--workspace", required=True)
    build = sub.add_parser("build")
    build.add_argument("--recipe", required=True)
    build.add_argument("--output", required=True)
    check = sub.add_parser("verify")
    check.add_argument("directory")
    for cmd in (build, check):
        cmd.add_argument("--reserve-bytes", type=int, default=1024**3)
        cmd.add_argument("--allow-drafts", action="store_true")
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--manifest", required=True)
    fetch.add_argument("--expected-sha256", required=True)
    fetch.add_argument("--sources", required=True)
    fetch.add_argument("--destination", required=True)
    fetch.add_argument("--allow-host", action="append", default=[])
    fetch.add_argument("--allow-loopback", action="store_true")
    fetch.add_argument("--timeout", type=int, default=60)
    install = sub.add_parser("import")
    install.add_argument("package")
    install.add_argument("--output", required=True)
    install.add_argument("--expected-sha256", required=True)
    installed = sub.add_parser("verify-install")
    installed.add_argument("directory")
    for cmd in (fetch, install):
        cmd.add_argument("--profile", choices=PROFILES, default="viewer")
    for cmd in (fetch, install, installed):
        cmd.add_argument("--allow-drafts", action="store_true")
        cmd.add_argument("--reserve-bytes", type=int, default=1024**3)
    prepare = sub.add_parser("prepare-workflow")
    prepare.add_argument("--workspace", required=True)
    prepare.add_argument("--installation", required=True)
    prepare.add_argument("--model", required=True)
    prepare.add_argument("--routing-revision", required=True)
    prepare.add_argument("--fcc-url", default="http://127.0.0.1:8082")
    pilot = sub.add_parser("export-pilot")
    pilot.add_argument("--selection", required=True)
    pilot.add_argument("--corpus", required=True)
    pilot.add_argument("--output", required=True)
    pilot.add_argument("--reserve-bytes", type=int, default=1024**3)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            result = diagnose(args.workspace, args.require_docker, args.component)
            print(json.dumps(result, indent=2))
            return 0 if result["passed"] else 2
        if args.command == "prepare-workflow":
            result = prepare_workflow(
                args.workspace, args.installation, args.model, args.routing_revision, args.fcc_url
            )
        elif args.command == "export-pilot":
            result = export_pilot(args.selection, args.corpus, args.output, args.reserve_bytes)
        elif args.command == "demo":
            result = demo_bundle(args.output, args.reserve_bytes)
        elif args.command == "init":
            config, storage = load_workspace(args.workspace)
            for relative in config["paths"].values():
                storage.mkdir(relative)
            result = dict(root=str(storage.root), paths=config["paths"])
        elif args.command == "build":
            result = build_release(args.recipe, args.output, args.reserve_bytes, args.allow_drafts)
        elif args.command == "fetch":
            result = fetch_release(
                args.manifest,
                args.expected_sha256,
                args.sources,
                args.destination,
                PROFILES[args.profile],
                args.allow_drafts,
                args.allow_host,
                args.allow_loopback,
                args.timeout,
                args.reserve_bytes,
            )
        elif args.command == "import":
            result = import_release(
                args.package,
                args.output,
                PROFILES[args.profile],
                args.allow_drafts,
                args.reserve_bytes,
                args.expected_sha256,
            )
        elif args.command == "verify-install":
            result = verify_install(args.directory, args.allow_drafts, args.reserve_bytes)
        else:
            result = verify_release(
                args.directory, allow_drafts=args.allow_drafts, reserve_bytes=args.reserve_bytes
            )
        print(json.dumps(result, indent=2))
        return 0
    except Paused as exc:
        print(json.dumps({"paused": str(exc), "retry_after": exc.retry_after}))
        return 3
    except KeyboardInterrupt:
        return 130
    except ValidationError as exc:
        print(
            json.dumps(
                {
                    "error": "Invalid release contract",
                    "validator": exc.validator,
                    "path": list(exc.absolute_path)[:16],
                }
            )
        )
        return 2
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc)}))
        return 2
