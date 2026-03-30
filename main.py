#!/usr/bin/env python3
"""PyQOA – cross-platform OpenAI chat client built with PyQt6."""

import sys
from pathlib import Path

# Ensure project root is importable when run directly
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPalette, QColor, QFont
from PyQt6.QtCore import Qt

from settings import Settings
from database import Database
from ui.main_window import MainWindow


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(0x21, 0x21, 0x21))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(0xEC, 0xEC, 0xF1))
    p.setColor(QPalette.ColorRole.Base,            QColor(0x1A, 0x1B, 0x26))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(0x2D, 0x37, 0x48))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(0x1F, 0x29, 0x37))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(0xEC, 0xEC, 0xF1))
    p.setColor(QPalette.ColorRole.Text,            QColor(0xEC, 0xEC, 0xF1))
    p.setColor(QPalette.ColorRole.Button,          QColor(0x37, 0x41, 0x51))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(0xEC, 0xEC, 0xF1))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(0xFF, 0xFF, 0xFF))
    p.setColor(QPalette.ColorRole.Link,            QColor(0x60, 0xA5, 0xFA))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(0x25, 0x63, 0xEB))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0xFF, 0xFF, 0xFF))
    # Disabled
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(0x6B, 0x72, 0x80))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(0x6B, 0x72, 0x80))
    return p


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PyQOA")
    app.setApplicationDisplayName("PyQOA")
    app.setOrganizationName("pyqoa")
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())

    font = app.font()
    font.setPointSize(10)
    app.setFont(font)

    settings = Settings()
    db = Database(settings.db_path)

    window = MainWindow(settings, db)
    window.show()

    try:
        sys.exit(app.exec())
    finally:
        db.close()


if __name__ == "__main__":
    main()
