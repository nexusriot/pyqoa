import sys
import os

from PyQt6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QSizePolicy
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import text_to_html

# Approximate prices per 1M tokens (input, output) in USD — may be outdated
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o":          (2.50,  10.00),
    "gpt-4o-mini":     (0.15,   0.60),
    "gpt-4-turbo":    (10.00,  30.00),
    "gpt-4":          (30.00,  60.00),
    "gpt-3.5-turbo":   (0.50,   1.50),
    "o1":             (15.00,  60.00),
    "o1-mini":         (3.00,  12.00),
    "o3-mini":         (1.10,   4.40),
    "o3":             (10.00,  40.00),
}


def _estimate_cost(model: str, prompt: int, completion: int) -> float | None:
    model_lc = model.lower()
    for key, (in_p, out_p) in _MODEL_PRICES.items():
        if key in model_lc:
            return (prompt * in_p + completion * out_p) / 1_000_000
    return None


_CONTENT_CSS = """
body {
    margin: 0; padding: 0;
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 14px;
    color: #ececf1;
    line-height: 1.6;
}
/* Fallback for unprocessed pre blocks */
pre {
    background: #0d1117;
    padding: 12px 16px;
    border-radius: 8px;
    border: 1px solid #2d3748;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 10px 0;
    font-family: 'Cascadia Code', 'Fira Code', Consolas, monospace;
    font-size: 13px;
    color: #f8f8f2;
}
/* Inline code */
code {
    font-family: 'Cascadia Code', 'Fira Code', Consolas, monospace;
    font-size: 13px;
    background: #1e2d3d;
    color: #e2e8f0;
    padding: 2px 6px;
    border-radius: 4px;
}
p { margin: 4px 0; }
h1 { font-size: 20px; margin: 12px 0 6px; color: #f1f5f9; border-bottom: 1px solid #1e293b; padding-bottom: 4px; }
h2 { font-size: 17px; margin: 10px 0 5px; color: #f1f5f9; }
h3 { font-size: 15px; margin: 8px 0 4px; color: #e2e8f0; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
td, th { border: 1px solid #334155; padding: 6px 12px; }
th { background: #1e293b; color: #94a3b8; font-weight: 600; }
blockquote {
    border-left: 3px solid #3b82f6;
    margin: 8px 0;
    padding: 4px 14px;
    color: #94a3b8;
    background: #0f172a;
    border-radius: 0 6px 6px 0;
}
a { color: #60a5fa; }
ul, ol { margin: 6px 0; padding-left: 22px; }
li { margin: 2px 0; }
hr { border: none; border-top: 1px solid #1e293b; margin: 12px 0; }
strong { color: #f1f5f9; }
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
        "user":      "background:#1e2d45;border-radius:10px;border-left:3px solid #3b82f6;",
        "assistant": "background:#161b2e;border-radius:10px;border-left:3px solid #10b981;",
    }
    _ROLE_COLOR = {
        "user":      "#60a5fa",
        "assistant": "#34d399",
    }
    _ROLE_LABEL = {
        "user":      "You",
        "assistant": "Assistant",
    }

    def __init__(
        self,
        role: str,
        content: str = "",
        streaming: bool = False,
        model: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self._raw_text = content
        self._streaming = streaming
        self._model = model
        self._setup_ui()
        if not streaming and content:
            self._render(content)

    def _setup_ui(self):
        self.setStyleSheet(f"QFrame {{ {self._FRAME_STYLE[self.role]} }}")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 8)
        lay.setSpacing(4)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        role_lbl = QLabel(self._ROLE_LABEL[self.role])
        role_lbl.setStyleSheet(
            f"color:{self._ROLE_COLOR[self.role]};"
            f"font-weight:bold;font-size:12px;background:transparent;"
        )
        header_row.addWidget(role_lbl)
        header_row.addStretch()
        lay.addLayout(header_row)

        self.browser = _AutoTextEdit()
        self.browser.setStyleSheet(
            "QTextEdit { background:transparent; border:none; color:#ececf1; font-size:14px; }"
        )
        lay.addWidget(self.browser)

        if self.role == "assistant":
            self._token_label = QLabel()
            self._token_label.setStyleSheet(
                "color:#374151;font-size:11px;background:transparent;"
            )
            self._token_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._token_label.hide()
            lay.addWidget(self._token_label)
        else:
            self._token_label = None

    def set_token_info(self, prompt_tokens: int, completion_tokens: int):
        if self._token_label is None:
            return
        total = prompt_tokens + completion_tokens
        text = f"{prompt_tokens:,} prompt + {completion_tokens:,} completion = {total:,} tokens"
        cost = _estimate_cost(self._model, prompt_tokens, completion_tokens)
        if cost is not None:
            text += f"  ·  ${cost:.4f}"
        self._token_label.setText(text)
        self._token_label.show()

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
