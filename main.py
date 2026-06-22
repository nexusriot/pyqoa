#!/usr/bin/env python3
"""PyQOA – cross-platform OpenAI chat client built with PyQt6."""

import sys
from pathlib import Path

# Ensure project root is importable when run directly
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication

import theme
from settings import Settings
from database import Database
from memory import ChatMemory
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PyQOA")
    app.setApplicationDisplayName("PyQOA")
    app.setOrganizationName("pyqoa")
    app.setStyle("Fusion")

    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    settings = Settings()
    db = Database(settings.db_path)
    memory = ChatMemory(settings, db)

    # Activate the saved theme before any widget is built. The stored value is a
    # preference ("system"/"light"/"dark"); resolve "system" to the OS scheme.
    theme.apply(theme.resolve(settings.get("theme", "dark")))
    app.setPalette(theme.qpalette())
    app.setStyleSheet(theme.global_qss())

    window = MainWindow(settings, db, memory)
    window.show()

    try:
        sys.exit(app.exec())
    finally:
        db.close()


if __name__ == "__main__":
    main()
