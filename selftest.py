"""Headless self-check of the bundled feature surface.

Reachable as `pyqoa --selftest`, this exists mainly to verify a *frozen* build:
PyInstaller can silently drop a Markdown extension, a Pygments lexer, a Qt
imageformat plugin or a keyring backend, and none of those failures are visible
until a user hits them. Running the same checks from a source tree is a cheap
sanity test too.

Prints one line per check and exits non-zero if a required one failed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

REQUIRED = "required"
OPTIONAL = "optional"

# The QApplication must outlive the checks: dropping the last reference to it
# tears Qt down again, and the next QPixmap aborts the process.
_app = None


def _check_qt() -> tuple[bool, str]:
    global _app
    from PyQt6.QtCore import QT_VERSION_STR
    from PyQt6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(["pyqoa"])
    return True, f"Qt {QT_VERSION_STR}"


def _check_markdown() -> tuple[bool, str]:
    import utils

    html, codes = utils.render_markdown(
        "| a | b |\n| - | - |\n| 1 | 2 |\n\n```python\nprint('hi')\n```"
    )
    if "<table>" not in html:
        return False, "the tables extension did not load"
    if codes != ["print('hi')\n"]:
        return False, "fenced code was not extracted"
    if utils._PYGMENTS and "color" not in html:
        return False, "Pygments is present but produced no highlighting"
    return True, "tables, fenced code and highlighting all present"


def _check_icon() -> tuple[bool, str]:
    from PyQt6.QtGui import QIcon

    import utils

    path = utils.asset_path("icons/pyqoa.svg")
    if not path.exists():
        return False, f"icon asset missing at {path}"
    if QIcon(str(path)).pixmap(64, 64).isNull():
        return False, "icon found but the SVG image plugin cannot render it"
    return True, str(path)


def _check_database() -> tuple[bool, str]:
    from database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        try:
            chat = db.create_chat("selftest")
            db.add_message(chat, "user", "the quick brown fox")
            if not db.search_messages("brown"):
                return False, "search returned nothing"
            return True, (
                "FTS5 index active" if db.fts_enabled else "LIKE fallback (no FTS5)"
            )
        finally:
            db.close()


def _check_pdf() -> tuple[bool, str]:
    import chat_io
    from database import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        try:
            chat = db.create_chat("selftest")
            db.add_message(chat, "user", "hello")
            db.add_message(chat, "assistant", "```py\nprint(1)\n```", model="gpt-4o")
            out = Path(tmp) / "out.pdf"
            chat_io.export_pdf(db, chat, out)
            if not out.read_bytes().startswith(b"%PDF"):
                return False, "writer produced something that is not a PDF"
            return True, f"{out.stat().st_size} bytes"
        finally:
            db.close()


def _check_tokens() -> tuple[bool, str]:
    import tokens

    count = tokens.count_tokens("hello world")
    if count <= 0:
        return False, "counted zero tokens"
    if tokens.exact_counting_available():
        return True, f"tiktoken available ({count} tokens for 'hello world')"
    return True, "heuristic counting (tiktoken not bundled)"


def _check_keyring() -> tuple[bool, str]:
    import keystore

    if keystore.available():
        return True, "an OS keyring backend is usable"
    return True, "no keyring backend — keys stay in settings.json"


def _check_vector_memory() -> tuple[bool, str]:
    from memory import chroma_available

    if chroma_available():
        return True, "chromadb available"
    return True, "chromadb not bundled — vector recall disabled"


CHECKS: list[tuple[str, str, object]] = [
    ("Qt runtime", REQUIRED, _check_qt),
    ("Markdown rendering", REQUIRED, _check_markdown),
    ("Application icon", REQUIRED, _check_icon),
    ("Database & search", REQUIRED, _check_database),
    ("PDF export", REQUIRED, _check_pdf),
    ("Token counting", REQUIRED, _check_tokens),
    ("OS keyring", OPTIONAL, _check_keyring),
    ("Vector memory", OPTIONAL, _check_vector_memory),
]


def run() -> int:
    """Run every check, print a report, and return a process exit code."""
    # A self-test must not need a display, even when one exists.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from version import __version__

    print(f"PyQOA {__version__} self-test")
    failures = 0
    for name, kind, check in CHECKS:
        try:
            ok, detail = check()
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        if ok:
            status = "ok"
        elif kind == OPTIONAL:
            status = "warn"
        else:
            status = "FAIL"
            failures += 1
        print(f"  [{status:>4}] {name:<20} {detail}")
    print("self-test failed" if failures else "self-test passed")
    return 1 if failures else 0
