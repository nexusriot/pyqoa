#!/usr/bin/env bash
#
# Pack the built bundle into a portable tarball that runs from anywhere.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

VERSION=$("${PYTHON:-python3}" -c "import sys; sys.path.insert(0, '.'); from version import __version__; print(__version__)")
ARCH=$(uname -m)
NAME=pyqoa-$VERSION-linux-$ARCH
STAGE=build/tarball/$NAME

if [ ! -x dist/pyqoa/pyqoa ]; then
    echo "build-tarball: no build found — run 'make binary' first" >&2
    exit 1
fi

rm -rf "build/tarball"
mkdir -p "$STAGE"
cp -a dist/pyqoa/. "$STAGE/"
cp README.md DESIGN.md "$STAGE/"
cp packaging/icons/pyqoa.svg packaging/pyqoa.desktop "$STAGE/"

mkdir -p dist
tar -C build/tarball -czf "dist/$NAME.tar.gz" "$NAME"
echo "build-tarball: dist/$NAME.tar.gz ($(du -h "dist/$NAME.tar.gz" | cut -f1))"
