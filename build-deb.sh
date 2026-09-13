#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=${VERSION:-0.3.0-1}
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
Depends: python3, python3-gi, python3-cryptography, gir1.2-gtk-3.0, dpkg, packagekit, pkexec, xdg-user-dirs
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
