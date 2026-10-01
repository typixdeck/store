#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=${VERSION:-0.3.2-1}
STAGE="$ROOT/build/package"
DIST="$ROOT/dist"
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/lib/python3/dist-packages" "$STAGE/usr/share/typix-store" "$STAGE/usr/share/applications" "$STAGE/usr/share/doc/typix-store" "$DIST"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: typix-store
Version: $VERSION
Architecture: all
Maintainer: TypixDeck <dev@typixnode.com>
Section: admin
Priority: optional
Depends: python3, python3-gi, python3-cryptography, gir1.2-gtk-3.0, librsvg2-common, dpkg, packagekit, pkexec, xdg-user-dirs
Recommends: typix-launcher (>= 0.3.1)
X-Typix-Compatible-OS: raspios-bookworm, raspios-trixie
Description: TypixDeck application store client
 Native GTK3 App Store front-end for a signed TypixDeck deb registry.
 It performs offline catalog validation, device compatibility checks,
 hash verification, and delegates privileged installation to PackageKit.
CONTROL
printf '%s\n' 'Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/' 'Upstream-Name: typix-store' > "$STAGE/usr/share/doc/typix-store/copyright"
cat > "$STAGE/usr/bin/typix-store" <<'RUNNER'
#!/bin/sh
set -eu
exec /usr/bin/python3 -m typix_store "$@"
RUNNER
chmod 755 "$STAGE/usr/bin/typix-store"
cp -R "$ROOT/src/typix_store" "$STAGE/usr/lib/python3/dist-packages/"
find "$STAGE/usr/lib/python3/dist-packages" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Catalog artwork belongs to Store's private directory. Other applications own
# their desktop icon paths; sharing those paths would make dpkg reject upgrades.
# Validate the fixed local manifest and copy checked bytes without network.
python3 - "$ROOT/assets/catalog-icons" "$STAGE" <<'ICONS'
from pathlib import Path
import hashlib
import json
import re
import stat
import sys

source, stage = map(Path, sys.argv[1:])
manifest_bytes = (source / "manifest.json").read_bytes()
manifest = json.loads(manifest_bytes)
if manifest.get("schema") != 1 or not isinstance(manifest.get("icons"), dict):
    raise SystemExit("Invalid catalog icon manifest")
destination = stage / "usr/share/typix-store/catalog-icons"
destination.mkdir(parents=True, exist_ok=True)
seen = set()
for package, row in manifest["icons"].items():
    filename = row.get("filename", "")
    name = row.get("iconName", "")
    if (not re.fullmatch(r"typix-[a-z0-9-]+", package)
            or not re.fullmatch(r"typix-[a-z0-9-]+\.(svg|png)", filename)
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name)
            or filename in seen):
        raise SystemExit("Invalid catalog icon identity")
    seen.add(filename)
    path = source / filename
    if not stat.S_ISREG(path.lstat().st_mode):
        raise SystemExit("Catalog icon must be a regular file")
    data = path.read_bytes()
    if (not 0 < len(data) <= 32 * 1024 * 1024 or len(data) != row.get("bytes")
            or hashlib.sha256(data).hexdigest() != row.get("sha256")):
        raise SystemExit("Catalog icon hash or size mismatch")
    target = destination / filename
    target.write_bytes(data)
    target.chmod(0o644)
    if package == "typix-store":
        size = "scalable" if filename.endswith(".svg") else "256x256"
        target = stage / "usr/share/icons/hicolor" / size / "apps" / (package + path.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Only Store's own theme icon is exported. Catalog rows are rendered
        # directly from private files, including before the app is installed.
        target.symlink_to("../../../../typix-store/catalog-icons/" + filename)
target = destination / "manifest.json"
target.write_bytes(manifest_bytes)
target.chmod(0o644)
for name in ("README.md", "LICENSE-original-vectors.txt"):
    target = destination / name
    target.write_bytes((source / name).read_bytes())
    target.chmod(0o644)
ICONS
# The independently signed development repository is deployed separately. Do
# not ship the historical unsigned seed as a catalog users can install from.
if [ -f "$ROOT/config/keys/development.pem" ]; then
    mkdir -p "$STAGE/usr/share/typix-store/keys"
    install -m 644 "$ROOT/config/keys/development.pem" "$STAGE/usr/share/typix-store/keys/development.pem"
fi
mkdir -p "$STAGE/usr/libexec"
install -m 755 "$ROOT/packaging/typix-store-stage" "$STAGE/usr/libexec/typix-store-stage"
install -m 644 "$ROOT/packaging/typix-store.desktop" "$STAGE/usr/share/applications/typix-store.desktop"
dpkg-deb --root-owner-group --build "$STAGE" "$DIST/typix-store_${VERSION}_all.deb"
