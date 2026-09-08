# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PyQOA.

Builds either a one-directory bundle (default, what the .deb ships) or a single
executable, selected with `pyinstaller --` arguments handled below:

    pyinstaller packaging/pyqoa.spec -- --onefile

`chromadb` is deliberately *not* bundled: it drags in onnxruntime and a model
download, which would multiply the download size for an optional feature. Vector
memory therefore needs a source install; everything else works in the binary.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH).resolve()
ROOT = SPEC_DIR.parent

sys.path.insert(0, str(ROOT))
from version import __version__  # noqa: E402

ONEFILE = "--onefile" in sys.argv

hiddenimports = [
    # Markdown loads its extensions by dotted string, so PyInstaller's static
    # analysis cannot see them.
    "markdown.extensions.fenced_code",
    "markdown.extensions.tables",
    "markdown.extensions.nl2br",
    "markdown.extensions.sane_lists",
    # tiktoken finds its encodings through a plugin namespace package.
    "tiktoken_ext",
    "tiktoken_ext.openai_public",
]
# keyring discovers backends at runtime; without them the keyring option would
# always report "unavailable" in a frozen build.
hiddenimports += collect_submodules("keyring.backends")

datas = [
    (str(SPEC_DIR / "icons" / "pyqoa.svg"), "icons"),
]

excludes = [
    "chromadb",
    "onnxruntime",
    # Pulled in transitively by the optional stack above; nothing PyQOA itself
    # uses needs them, and together they are ~60 MB.
    "numpy",
    "uvloop",
    "yaml",
    "tkinter",
    "matplotlib",
    "IPython",
    "pytest",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtBluetooth",
    "PyQt6.QtMultimedia",
    "PyQt6.QtQuick",
    "PyQt6.QtQml",
    "PyQt6.Qt3DCore",
    "PyQt6.QtCharts",
    "PyQt6.QtDataVisualization",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        name="pyqoa",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        version=__version__,
        icon=None,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="pyqoa",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        version=__version__,
        icon=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="pyqoa",
    )
