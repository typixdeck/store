"""Offline catalog logos from a bounded, package-keyed bundled manifest."""
import hashlib
import json
from pathlib import Path
import re


def icon_file(package, root=None):
    root = root or Path("/usr/share/typix-store/catalog-icons")
    if not root.is_dir():
        root = Path(__file__).resolve().parents[2] / "assets/catalog-icons"
    try:
        manifest = root / "manifest.json"
        if manifest.stat().st_size > 64 * 1024:
            return None
        data = json.loads(manifest.read_bytes())
        item = data["icons"][package]
        name = item["filename"]
        size = item["bytes"]
        if (type(data["schema"]) is not int or data["schema"] != 1 or type(size) is not int
                or not 0 < size <= 2 * 1024 * 1024 or not isinstance(name, str)
                or not re.fullmatch(r"[A-Za-z0-9._-]+\.(png|svg)", name)):
            return None
        path = root / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
            return None
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            return None
        return path
    except (OSError, ValueError, KeyError, TypeError):
        return None
