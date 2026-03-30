import sys
import os

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QTextEdit, QSizePolicy
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor

# Allow importing from parent package when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import text_to_html

_CONTENT_CSS = """
body {
    margin: 0; padding: 0;
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
    color: #ececf1;
    line-height: 1.6;
}
pre {
    background: #0d1117;
    padding: 10px 14px;
    border-radius: 6px;
    overflow-x: auto;
    white-space: pre-wrap;
    word-break: break-word;
}
code {
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 13px;
}
p { margin: 4px 0; }
h1, h2, h3 { margin: 8px 0 4px; }
table { border-collapse: collapse; width: 100%; margin: 6px 0; }
td, th { border: 1px solid #4b5563; padding: 5px 10px; }
th { background: #1f2937; }
blockquote {
    border-left: 3px solid #4b5563;
    margin: 4px 0;
    padding-left: 12px;
    color: #9ca3af;
}
a { color: #60a5fa; }
ul, ol { margin: 4px 0; padding-left: 20px; }
"""


class _AutoTextEdit(QTextEdit):
    """Read-only QTextEdit that auto-sizes its height to fit content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setFrameStyle(0)
        self.document().contentsChanged.connect(self._schedule_resize)

    def _schedule_resize(self):
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        vw = self.viewport().width()
        if vw < 10:
            return
        self.document().setTextWidth(vw)
        h = int(self.document().size().height()) + 6
        self.setFixedHeight(max(h, 24))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class MessageWidget(QFrame):
    """A single chat bubble (user or assistant)."""

    _FRAME_STYLE = {
        "user":      "background:#2a3447; border-radius:10px;",
        "assistant": "background:#1a1d2e; border-radius:10px;",
    }
    _ROLE_COLOR = {
        "user":      "#60a5fa",
        "assistant": "#34d399",
    }
    _ROLE_LABEL = {
        "user":      "You",
        "assistant": "Assistant",
    }

    def __init__(self, role: str, content: str = "", streaming: bool = False, parent=None):
        super().__init__(parent)
        self.role = role
        self._raw_text = content
        self._streaming = streaming
        self._setup_ui()
        if streaming:
            # Blank slate – chunks will arrive via append_chunk()
            pass
        elif content:
            self._render(content)

    def _setup_ui(self):
        self.setStyleSheet(f"QFrame {{ {self._FRAME_STYLE[self.role]} }}")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(4)

        lbl = QLabel(self._ROLE_LABEL[self.role])
        lbl.setStyleSheet(
            f"color:{self._ROLE_COLOR[self.role]};"
            f"font-weight:bold;font-size:12px;background:transparent;"
        )
        lay.addWidget(lbl)

        self.browser = _AutoTextEdit()
        self.browser.setStyleSheet(
            "QTextEdit { background:transparent; border:none; color:#ececf1; font-size:14px; }"
        )
        lay.addWidget(self.browser)

    def append_chunk(self, text: str):
        """Fast plain-text append during streaming."""
        self._raw_text += text
        cursor = self.browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.browser.setTextCursor(cursor)

    def finalize(self):
        """Re-render with full Markdown once streaming is done."""
        self._streaming = False
        self._render(self._raw_text)

    def get_text(self) -> str:
        return self._raw_text

    def _render(self, text: str):
        body = text_to_html(text)
        full = f"<html><head><style>{_CONTENT_CSS}</style></head><body>{body}</body></html>"
        self.browser.setHtml(full)
