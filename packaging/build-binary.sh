#!/usr/bin/env bash
#
# Build the PyInstaller bundle.
#
#   build-binary.sh [--onefile]
#
# Output: dist/pyqoa/ (one-directory, the default) or dist/pyqoa (single file).
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

PYTHON=${PYTHON:-python3}
MODE=${1:-}

if ! "$PYTHON" -c "import PyInstaller" >/dev/null 2>&1; then
    echo "build-binary: PyInstaller is missing — run 'make deps-build'" >&2
    exit 1
fi

# One-file and one-directory builds are kept in separate dist subtrees: both
# want the name "pyqoa", and a file cannot share a path with a directory.
extra=()
DISTPATH=$ROOT/dist
if [ "$MODE" = "--onefile" ]; then
    extra=(-- --onefile)
    DISTPATH=$ROOT/dist/onefile
elif [ -n "$MODE" ]; then
    echo "build-binary: unknown option '$MODE' (expected --onefile)" >&2
    exit 2
fi

# A stale work directory is the usual cause of "it built but the old code ran".
"$PYTHON" -m PyInstaller \
    --noconfirm --clean \
    --distpath "$DISTPATH" \
    --workpath "$ROOT/build/pyinstaller" \
    "$ROOT/packaging/pyqoa.spec" \
    "${extra[@]}"

if [ "$MODE" = "--onefile" ]; then
    echo "build-binary: dist/onefile/pyqoa ($(du -h dist/onefile/pyqoa | cut -f1))"
else
    echo "build-binary: dist/pyqoa/ ($(du -sh dist/pyqoa | cut -f1))"
fi
