"""Explicit distribution operations; importing this module never starts work."""

import argparse
import json

from .distribution_contracts import load_workspace
from .distribution_release import build_release, verify_release


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
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
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "init":
            config, storage = load_workspace(args.workspace)
            for relative in config["paths"].values():
                storage.mkdir(relative)
            result = dict(root=str(storage.root), paths=config["paths"])
        elif args.command == "build":
            result = build_release(args.recipe, args.output, args.reserve_bytes, args.allow_drafts)
        else:
            result = verify_release(
                args.directory, allow_drafts=args.allow_drafts, reserve_bytes=args.reserve_bytes
            )
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc)}))
        return 2
