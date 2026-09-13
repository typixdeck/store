#!/usr/bin/env python3
"""Package the reviewed complete-deb source as a signed local Store repository."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from store_publication import DEFAULT_SOURCE, ROOT, load_key, write_publication


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="0.3.0-1", help="Offline repository package version, independent of contained app versions")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument("--key", type=Path, default=Path.home() / ".local/share/typixdeck/signing/development.pem")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+~:-]*", args.version):
        parser.error("Invalid repository package version")
    key = load_key(args.key, create=True)
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="typix-repository-") as temporary:
        stage = Path(temporary)
        repository = stage / "usr/share/typix-store/repository"
        packages = repository / "packages"
        keys = stage / "usr/share/typix-store/keys"
        publication = stage / "publication"
        write_publication(args.source, publication, key, channel="development-offline")
        packages.mkdir(parents=True)
        keys.mkdir(parents=True)
        for path in publication.glob("*.deb"):
            shutil.move(path, packages / path.name)
        for filename in ("catalog.json", "catalog.json.sig"):
            shutil.move(publication / filename, repository / filename)
        shutil.move(publication / "public-key.pem", keys / "development.pem")
        shutil.rmtree(publication)
        control = stage / "DEBIAN"
        control.mkdir()
        (control / "control").write_text(
            f"Package: typix-store-offline-repository\nVersion: {args.version}\nArchitecture: all\n"
            "Maintainer: TypixDeck <dev@typixnode.com>\nSection: misc\nPriority: optional\n"
            "Depends: typix-store (>= 0.3.0)\nX-Typix-Compatible-OS: raspios-bookworm,raspios-trixie\n"
            "Description: Signed complete-application offline source for TypixDeck\n"
            " Contains the original complete debs from the reviewed source manifest.\n"
            " Gamer requires user-provided legitimate ROMs and compatible cores.\n"
            " Incomplete MyAI launch wrappers and private signing keys are excluded.\n", encoding="utf-8")
        result = args.output / f"typix-store-offline-repository_{args.version}_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(result)], check=True)
        print(result)


if __name__ == "__main__":
    main()
