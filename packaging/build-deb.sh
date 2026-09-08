#!/usr/bin/env bash
#
# Build a self-contained .deb from the PyInstaller bundle.
#
#   build-deb.sh [revision]
#
# The package ships the frozen application under /usr/lib/pyqoa, so it depends
# only on a handful of system libraries (Qt and Python come bundled) and works
# across Debian/Ubuntu releases that would never agree on python3-pyqt6 and
# python3-openai versions.
#
# Needs `dpkg-deb` only — no debhelper, no fakeroot (--root-owner-group).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

REVISION=${1:-1}
VERSION=$("${PYTHON:-python3}" -c "import sys; sys.path.insert(0, '.'); from version import __version__; print(__version__)")
ARCH=$(dpkg --print-architecture)
MAINTAINER=${DEB_MAINTAINER:-"PyQOA maintainers <nobody@example.com>"}
PKGNAME=pyqoa
STAGE=build/deb/${PKGNAME}_${VERSION}-${REVISION}_${ARCH}

for tool in dpkg-deb dpkg gzip; do
    command -v "$tool" >/dev/null || { echo "build-deb: $tool is required" >&2; exit 1; }
done

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN"

packaging/install-tree.sh "$STAGE" /usr

INSTALLED_KB=$(du -sk "$STAGE" | cut -f1)

# Everything else the bundle needs is vendored inside it — these are the only
# libraries it resolves from the system (verified with ldd over the bundle).
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: $PKGNAME
Version: $VERSION-$REVISION
Section: utils
Priority: optional
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_KB
Depends: libc6, libgl1, libxcb1, libglib2.0-0 | libglib2.0-0t64
Recommends: libxkbcommon-x11-0, fonts-dejavu-core
Homepage: https://github.com/nexusriot/pyqoa
Description: Desktop chat client for OpenAI-compatible APIs
 PyQOA is a PyQt6 desktop client for any OpenAI-compatible endpoint: the OpenAI
 cloud, a local Ollama server, OpenRouter, or a custom gateway. Conversations
 are stored locally in SQLite and can be searched full-text, branched, exported
 and backed up.
 .
 Features include streaming replies with live Markdown, named provider profiles,
 tool calling over built-ins and MCP servers, image and file attachments,
 per-chat model overrides, token and cost tracking, and light/dark theming.
 .
 This package bundles its own Python and Qt runtime. The optional vector-memory
 feature (chromadb) is not bundled; install PyQOA from source to use it.
CONTROL

cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
POSTINST

cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
    fi
fi
exit 0
POSTRM

chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

docdir=$STAGE/usr/share/doc/$PKGNAME
install -d "$docdir"
cat > "$docdir/copyright" <<'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: pyqoa
Source: https://github.com/nexusriot/pyqoa

Files: *
Copyright: 2026 The PyQOA authors
License: see-upstream
 No licence has been declared upstream yet. Add one before distributing this
 package.
COPYRIGHT

# Acknowledge the tags that are inherent to shipping a frozen bundle: the point
# of this package is that it carries its own Python and Qt.
lintiandir=$STAGE/usr/share/lintian/overrides
install -d "$lintiandir"
cat > "$lintiandir/$PKGNAME" <<'OVERRIDES'
# PyQOA ships a self-contained PyInstaller bundle under /usr/lib/pyqoa so that it
# installs on any Debian or Ubuntu release without matching python3-pyqt6 and
# python3-openai versions. Vendoring the runtime is the deliberate trade-off.
pyqoa: embedded-library *
pyqoa: library-not-linked-against-libc *
pyqoa: shared-library-lacks-prerequisites *
# The bundled wheels' extension modules are shipped as their upstream builds;
# stripping third-party binaries we did not compile risks breaking them.
pyqoa: unstripped-binary-or-object *
# The PyInstaller bootloader is not built as PIE.
pyqoa: hardening-no-pie *
# Not an upload to the Debian archive.
pyqoa: initial-upload-closes-no-bugs
OVERRIDES
chmod 644 "$lintiandir/$PKGNAME"

printf '%s (%s-%s) unstable; urgency=low\n\n  * Packaged from the upstream source tree.\n\n -- %s  %s\n' \
    "$PKGNAME" "$VERSION" "$REVISION" "$MAINTAINER" "$(date -R)" \
    | gzip -9n > "$docdir/changelog.Debian.gz"
chmod 644 "$docdir/changelog.Debian.gz" "$docdir/copyright"

# md5sums covers every shipped file; dpkg and lintian both expect it.
( cd "$STAGE" && find . -path ./DEBIAN -prune -o -type f -print0 \
    | sed -z 's|^\./||' | sort -z \
    | xargs -0 -r md5sum > DEBIAN/md5sums )

mkdir -p dist
OUT=dist/${PKGNAME}_${VERSION}-${REVISION}_${ARCH}.deb
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "build-deb: $OUT ($(du -h "$OUT" | cut -f1))"
