#!/usr/bin/env python3
"""PyQOA – cross-platform OpenAI-compatible chat client built with PyQt6."""

import argparse
import sys
from pathlib import Path

# Ensure project root is importable when run directly
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtGui import QIcon
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import selftest
import theme
import utils
from database import Database
from memory import ChatMemory
from settings import Settings
from ui.main_window import MainWindow
from version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyqoa",
        description="Desktop chat client for any OpenAI-compatible API.",
    )
    parser.add_argument(
        "--version", action="version", version=f"PyQOA {__version__}"
    )
    parser.add_argument(
        "--data-dir", metavar="PATH",
        help="Use an alternative profile directory (settings, database, vectors).",
    )
    parser.add_argument(
        "--profile", metavar="NAME",
        help="Activate this provider profile on startup.",
    )
    parser.add_argument(
        "--new-chat", action="store_true",
        help="Start in a fresh chat instead of the most recent one.",
    )
    parser.add_argument(
        "--prompt", metavar="TEXT",
        help="Open a new chat and send TEXT immediately.",
    )
    parser.add_argument(
        "--selftest", action="store_true",
        help="Check the bundled feature surface headlessly and exit.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    if args.selftest:
        return selftest.run()

    app = QApplication(sys.argv[:1])
    app.setApplicationName("PyQOA")
    app.setApplicationDisplayName("PyQOA")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("pyqoa")
    app.setDesktopFileName("pyqoa")
    app.setStyle("Fusion")

    icon_path = utils.asset_path("icons/pyqoa.svg")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    settings = Settings(Path(args.data_dir) if args.data_dir else None)
    if args.profile and not settings.activate_profile(args.profile):
        print(
            f"pyqoa: unknown profile '{args.profile}'; "
            f"known profiles: {', '.join(settings.profile_names())}",
            file=sys.stderr,
        )
        return 2

    db = Database(settings.db_path)
    memory = ChatMemory(settings, db)

    # Activate the saved theme and font scale before any widget is built. The
    # stored theme value is a preference ("system"/"light"/"dark"); resolve
    # "system" to the OS scheme.
    theme.set_font_scale(settings.get("font_scale", 1.0))
    theme.apply(theme.resolve(settings.get("theme", "dark")))
    app.setPalette(theme.qpalette())
    app.setStyleSheet(theme.global_qss())

    font = app.font()
    font.setPointSize(theme.app_point_size())
    app.setFont(font)

    window = MainWindow(settings, db, memory)
    window.show()

    if args.prompt:
        QTimer.singleShot(0, lambda: _send_initial(window, args.prompt))
    elif args.new_chat:
        QTimer.singleShot(0, window._new_chat)

    try:
        return app.exec()
    finally:
        db.close()


def _send_initial(window, prompt: str):
    """Open a fresh chat and send a prompt handed in on the command line."""
    window._new_chat()
    window.chat_view.input_edit.setPlainText(prompt)
    window.chat_view._send()


if __name__ == "__main__":
    sys.exit(main())
