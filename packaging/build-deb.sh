#!/usr/bin/env bash
# Assemble and build the Looksee .deb (PLAN §5/§7 P6).
# Repo-relative paths only; output lands in gitignored buildout/.
#
#   packaging/build-deb.sh   ->  buildout/looksee_0.2.2_all.deb
set -euo pipefail

if [ -n "${BASH_SOURCE[0]:-}" ]; then here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; else here="$(pwd)"; fi
cd "$here/.."

STAGE=scratch/deb-root
OUT=buildout
DEB="$OUT/looksee_0.2.2_all.deb"

rm -rf "$STAGE"
mkdir -p "$STAGE/usr/bin" "$STAGE/usr/share/looksee" "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps" "$STAGE/usr/share/doc/looksee" \
         "$STAGE/DEBIAN" "$OUT"

install -m 755 packaging/looksee-wrapper.sh "$STAGE/usr/bin/looksee"
# The FTR3 split ships THREE python modules (shim + pure core + UI).
install -m 644 src/looksee.py "$STAGE/usr/share/looksee/looksee.py"
install -m 644 src/looksee_io.py "$STAGE/usr/share/looksee/looksee_io.py"
install -m 644 src/looksee_ui.py "$STAGE/usr/share/looksee/looksee_ui.py"
install -m 644 data/pro.krisforbes.Looksee.desktop "$STAGE/usr/share/applications/"
install -m 644 data/looksee.svg "$STAGE/usr/share/icons/hicolor/scalable/apps/looksee.svg"
install -m 644 README.md "$STAGE/usr/share/doc/looksee/README.md"
install -m 644 packaging/copyright "$STAGE/usr/share/doc/looksee/copyright"
chmod 644 packaging/DEBIAN/control
cp -r packaging/DEBIAN/. "$STAGE/DEBIAN/"
chmod 755 "$STAGE/DEBIAN" "$STAGE/usr" "$STAGE/usr/bin"

# libdpkg needs a writable temp dir (and a trailing slash); pin it repo-local
# so the build is hermetic under sandboxes with a read-only /tmp.
TMPDIR="$PWD/scratch/" dpkg-deb --build --root-owner-group "$STAGE" "$DEB"
sha256sum "$DEB" | tee "$DEB.sha256"
echo "BUILT: $DEB"
