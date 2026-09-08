#!/usr/bin/env bash
#
# Stage a built PyQOA bundle into a filesystem tree.
#
#   install-tree.sh DESTDIR [PREFIX]
#
# Both `make install` and the .deb build call this, so the on-disk layout is
# defined in exactly one place.
set -euo pipefail

DESTDIR=${1:?usage: install-tree.sh DESTDIR [PREFIX]}
PREFIX=${2:-/usr}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUNDLE=$ROOT/dist/pyqoa

if [ ! -x "$BUNDLE/pyqoa" ]; then
    echo "install-tree: no build found at $BUNDLE — run 'make binary' first" >&2
    exit 1
fi

libdir=$DESTDIR$PREFIX/lib/pyqoa
bindir=$DESTDIR$PREFIX/bin
appdir=$DESTDIR$PREFIX/share/applications
icondir=$DESTDIR$PREFIX/share/icons/hicolor/scalable/apps
docdir=$DESTDIR$PREFIX/share/doc/pyqoa
mandir=$DESTDIR$PREFIX/share/man/man1

install -d "$libdir" "$bindir" "$appdir" "$icondir" "$docdir" "$mandir"

# The one-directory bundle keeps its own layout; copy it verbatim.
cp -a "$BUNDLE/." "$libdir/"
# Normalise modes: a build tree inherits the developer's umask, and dpkg ships
# whatever it is handed (group-writable directories upset lintian).
chmod -R u=rwX,go=rX "$libdir"
# Shared objects are dlopen'd, not executed; Debian policy wants them 0644.
find "$libdir" -type f -name '*.so*' -exec chmod 644 {} +
chmod 755 "$libdir/pyqoa"

# A wrapper rather than a symlink: PyInstaller resolves its data directory from
# the executable's own path, and a wrapper keeps that unambiguous.
cat > "$bindir/pyqoa" <<WRAPPER
#!/bin/sh
exec $PREFIX/lib/pyqoa/pyqoa "\$@"
WRAPPER
chmod 755 "$bindir/pyqoa"

install -m 644 "$ROOT/packaging/pyqoa.desktop" "$appdir/pyqoa.desktop"
install -m 644 "$ROOT/packaging/icons/pyqoa.svg" "$icondir/pyqoa.svg"
install -m 644 "$ROOT/README.md" "$ROOT/DESIGN.md" "$docdir/"
gzip -9nc "$ROOT/packaging/pyqoa.1" > "$mandir/pyqoa.1.gz"
chmod 644 "$mandir/pyqoa.1.gz"

echo "install-tree: staged into $DESTDIR$PREFIX"
