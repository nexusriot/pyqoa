import sys
import os

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QPushButton,
    QSizePolicy, QApplication, QToolTip,
)
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QTextCursor, QCursor, QDesktopServices

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme
from utils import render_markdown

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
    # Match the most specific (longest) price key contained in the model name,
    # so e.g. "gpt-4o-mini" is not priced as "gpt-4o" and "o1-mini" not as "o1".
    best_key: str | None = None
    for key in _MODEL_PRICES:
        if key in model_lc and (best_key is None or len(key) > len(best_key)):
            best_key = key
    if best_key is None:
        return None
    in_p, out_p = _MODEL_PRICES[best_key]
    return (prompt * in_p + completion * out_p) / 1_000_000


def _content_css() -> str:
    """Document CSS for a message body. Built per render so it tracks the theme."""
    return f"""
body {{
    margin: 0; padding: 0;
    font-family: {theme.FONT_STACK};
    font-size: 14px;
    color: {theme.TEXT};
    line-height: 1.62;
}}
/* Fallback for unprocessed pre blocks (kept dark, matching styled code blocks) */
pre {{
    background: {theme.CODE_BG};
    padding: 12px 16px;
    border-radius: {theme.RADIUS_SM}px;
    border: 1px solid {theme.CODE_BORDER};
    white-space: pre-wrap;
    word-break: break-word;
    margin: 10px 0;
    font-family: {theme.MONO_STACK};
    font-size: 13px;
    color: {theme.CODE_FG};
}}
/* Inline code */
code {{
    font-family: {theme.MONO_STACK};
    font-size: 13px;
    background: {theme.SURFACE_HI};
    color: {theme.TEXT};
    padding: 2px 6px;
    border-radius: 4px;
}}
p {{ margin: 4px 0; }}
h1 {{ font-size: 20px; margin: 12px 0 6px; color: {theme.TEXT}; border-bottom: 1px solid {theme.BORDER}; padding-bottom: 4px; }}
h2 {{ font-size: 17px; margin: 10px 0 5px; color: {theme.TEXT}; }}
h3 {{ font-size: 15px; margin: 8px 0 4px; color: {theme.TEXT}; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
td, th {{ border: 1px solid {theme.BORDER_HI}; padding: 6px 12px; }}
th {{ background: {theme.SURFACE_HI}; color: {theme.MUTED}; font-weight: 600; }}
blockquote {{
    border-left: 3px solid {theme.ACCENT};
    margin: 8px 0;
    padding: 4px 14px;
    color: {theme.MUTED};
    background: {theme.SURFACE_HI};
    border-radius: 0 6px 6px 0;
}}
a {{ color: {theme.ACCENT}; }}
ul, ol {{ margin: 6px 0; padding-left: 22px; }}
li {{ margin: 2px 0; }}
hr {{ border: none; border-top: 1px solid {theme.BORDER}; margin: 12px 0; }}
strong {{ color: {theme.TEXT}; }}
"""


class _AutoTextEdit(QTextBrowser):
    """Read-only text view that auto-sizes its height to fit content.

    Uses QTextBrowser (not QTextEdit) so that link clicks emit `anchorClicked`,
    which the message widget uses for the per-code-block "Copy" links.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenLinks(False)          # we handle clicks ourselves
        self.setOpenExternalLinks(False)
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

    edit_requested = pyqtSignal(int)        # emits the user message's DB id
    regenerate_requested = pyqtSignal(int)  # emits the assistant message's DB id

    # Theme-independent labels/glyphs (colours are resolved per-instance in _setup_ui,
    # so they always reflect the active theme — class-level colour dicts would freeze
    # whatever theme was active at import time).
    _ROLE_LABEL = {
        "user":      "You",
        "assistant": "Assistant",
    }
    _AVATAR_TEXT = {
        "user":      "U",
        "assistant": "✦",
    }

    def __init__(
        self,
        role: str,
        content: str = "",
        streaming: bool = False,
        model: str = "",
        message_id: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self._raw_text = content
        self._streaming = streaming
        self._model = model
        self.message_id = message_id
        self._code_blocks: list[str] = []
        self._setup_ui()
        if not streaming and content:
            self._render(content)
        self._update_actions_visibility()

    def _setup_ui(self):
        color = theme.USER if self.role == "user" else theme.ASSISTANT
        bubble_bg = theme.SURFACE_SEL if self.role == "user" else theme.SURFACE
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"MessageWidget {{ background:{bubble_bg};"
            f"border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS}px; }}"
        )
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 12, 16, 11)
        outer.setSpacing(12)

        # Circular role avatar pinned to the top-left.
        self._avatar = QLabel(self._AVATAR_TEXT[self.role])
        self._avatar.setFixedSize(30, 30)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(
            f"background:{color}; color:{theme.AVATAR_FG}; border-radius:15px;"
            f"font-weight:700; font-size:14px;"
        )
        avatar_col = QVBoxLayout()
        avatar_col.setContentsMargins(0, 0, 0, 0)
        avatar_col.addWidget(self._avatar)
        avatar_col.addStretch()
        outer.addLayout(avatar_col)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(2)

        role_lbl = QLabel(self._ROLE_LABEL[self.role])
        role_lbl.setStyleSheet(
            f"color:{color};font-weight:700;font-size:12.5px;background:transparent;"
        )
        header_row.addWidget(role_lbl)
        header_row.addStretch()

        # Per-message actions (hidden until the message is persisted / finalized).
        self._actions: list[QPushButton] = []
        self._copy_btn = self._make_action_btn("Copy", "Copy message text")
        self._copy_btn.clicked.connect(self._copy_message)
        header_row.addWidget(self._copy_btn)

        if self.role == "user":
            self._edit_btn = self._make_action_btn("Edit", "Edit and resend from here")
            self._edit_btn.clicked.connect(self._emit_edit)
            header_row.addWidget(self._edit_btn)
        else:
            self._regen_btn = self._make_action_btn("↻", "Regenerate this reply")
            self._regen_btn.clicked.connect(self._emit_regenerate)
            header_row.addWidget(self._regen_btn)

        col.addLayout(header_row)

        self.browser = _AutoTextEdit()
        # Don't set `color` here: a widget-level colour overrides inline HTML colours
        # for un-tokenised text, which breaks dark code blocks under the light theme.
        # The document's body CSS drives the body text colour instead.
        self.browser.setStyleSheet(
            "QTextBrowser { background:transparent; border:none; font-size:14px; }"
        )
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        col.addWidget(self.browser)

        if self.role == "assistant":
            self._token_label = QLabel()
            self._token_label.setStyleSheet(
                f"color:{theme.FAINT};font-size:11px;background:transparent;"
            )
            self._token_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._token_label.hide()
            col.addWidget(self._token_label)
        else:
            self._token_label = None

        outer.addLayout(col, stretch=1)

    def _make_action_btn(self, text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(22)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.FAINT};border:none;"
            f"font-size:11px;padding:0 7px;border-radius:6px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};background:{theme.SURFACE_HI};}}"
        )
        self._actions.append(btn)
        return btn

    def set_message_id(self, message_id: int):
        self.message_id = message_id
        self._update_actions_visibility()

    def _update_actions_visibility(self):
        """Show action buttons only for a persisted, non-streaming message."""
        visible = (not self._streaming) and (self.message_id is not None)
        for btn in getattr(self, "_actions", []):
            btn.setVisible(visible)

    def _copy_message(self):
        QApplication.clipboard().setText(self._raw_text)
        QToolTip.showText(QCursor.pos(), "Message copied", self)

    def _emit_edit(self):
        if self.message_id is not None:
            self.edit_requested.emit(self.message_id)

    def _emit_regenerate(self):
        if self.message_id is not None:
            self.regenerate_requested.emit(self.message_id)

    def _on_anchor_clicked(self, url: QUrl):
        s = url.toString()
        if s.startswith("pyqoacopy:"):
            try:
                idx = int(s.split(":", 1)[1])
            except ValueError:
                return
            if 0 <= idx < len(self._code_blocks):
                QApplication.clipboard().setText(self._code_blocks[idx])
                QToolTip.showText(QCursor.pos(), "Code copied", self)
        elif s:
            QDesktopServices.openUrl(url)

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
        self._update_actions_visibility()

    def get_text(self) -> str:
        return self._raw_text

    def _render(self, text: str):
        body, self._code_blocks = render_markdown(text)
        full = f"<html><head><style>{_content_css()}</style></head><body>{body}</body></html>"
        self.browser.setHtml(full)
