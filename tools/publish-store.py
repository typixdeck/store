#!/usr/bin/env python3
"""Validate and stage original complete debs for a signed GitHub Store source.

This command prepares files locally; it never commits, uploads or changes a device.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from store_publication import DEFAULT_SOURCE, PublicationError, inspect_packages, load_key, require, write_publication


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate manifest, control fields, hashes, desktop launcher and actual code/assets")
    validate.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    keygen = commands.add_parser("keygen", help="Create a private Ed25519 key outside the repository, mode 0600")
    keygen.add_argument("--key", type=Path, required=True)
    build = commands.add_parser("build", help="Prepare exact original debs and signed catalog; does not upload")
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--repository", required=True, help="Actual GitHub owner/repo; no default repository")
    build.add_argument("--mode", choices=("release", "raw"), default="release")
    build.add_argument("--key", type=Path, required=True)
    build.add_argument("--expires-days", type=int, default=90)
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            load_key(args.key, create=True)
            print(f"Signing key is ready outside the repository: {args.key}")
        elif args.command == "validate":
            entries = inspect_packages(args.source)
            print(json.dumps([{"package": entry["package"], "version": entry["currentVersion"],
                               **entry["versions"][0]["artifact"]} for entry in entries], ensure_ascii=False, indent=2))
        else:
            require(not args.key.expanduser().resolve().is_relative_to(args.output.resolve()),
                    "Private signing key must stay outside the publication output directory")
            key = load_key(args.key)
            hashes = write_publication(args.source, args.output, key,
                                       channel="github-repository" if args.mode == "raw" else "github-release",
                                       repository=args.repository, days=args.expires_days)
            print(json.dumps({"output": str(args.output), "repository": args.repository, "mode": args.mode,
                              "uploaded": False, "sha256": hashes}, ensure_ascii=False, indent=2))
    except (OSError, PublicationError) as exc:
        print(f"Publication refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
