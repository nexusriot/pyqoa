import sys
import os

from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser, QPushButton,
    QSizePolicy, QApplication, QToolTip, QWidget,
)
from PyQt6.QtCore import Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QTextCursor, QCursor, QDesktopServices, QPixmap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pricing
import theme
import utils
from utils import render_markdown

# Live Markdown re-rendering while streaming is throttled, and disabled entirely
# for very long replies where re-laying out the whole document per tick costs
# more than it gains.
LIVE_RENDER_MS = 220
LIVE_RENDER_MAX_CHARS = 24_000
THUMB_MAX = 190

def _content_css() -> str:
    """Document CSS for a message body. Built per render so it tracks the theme."""
    return f"""
body {{
    margin: 0; padding: 0;
    font-family: {theme.FONT_STACK};
    font-size: {theme.FS_BASE}px;
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
    font-size: {theme.FS_MD}px;
    color: {theme.CODE_FG};
}}
/* Inline code */
code {{
    font-family: {theme.MONO_STACK};
    font-size: {theme.FS_MD}px;
    background: {theme.SURFACE_HI};
    color: {theme.TEXT};
    padding: 2px 6px;
    border-radius: 4px;
}}
p {{ margin: 4px 0; }}
h1 {{ font-size: {theme.FS_TITLE - 2}px; margin: 12px 0 6px; color: {theme.TEXT}; border-bottom: 1px solid {theme.BORDER}; padding-bottom: 4px; }}
h2 {{ font-size: {theme.FS_LG + 1}px; margin: 10px 0 5px; color: {theme.TEXT}; }}
h3 {{ font-size: {theme.FS_ACTION}px; margin: 8px 0 4px; color: {theme.TEXT}; }}
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
    branch_requested = pyqtSignal(int)      # fork the chat at this message
    variant_requested = pyqtSignal(int)     # show this alternate reply instead

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
        live_markdown: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.role = role
        self._raw_text = content
        self._streaming = streaming
        self._model = model
        self.message_id = message_id
        self._code_blocks: list[str] = []
        self._live_markdown = bool(live_markdown)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._live_render)
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
            f"font-weight:700; font-size:{theme.FS_BASE}px;"
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
            f"color:{color};font-weight:700;font-size:{theme.FS_SM}px;background:transparent;"
        )
        header_row.addWidget(role_lbl)

        # Variant switcher: ‹ 2/3 › over alternate replies to the same prompt.
        self._variant_ids: list[int] = []
        self._variant_prev = QPushButton("‹")
        self._variant_prev.setToolTip("Previous alternative reply")
        self._variant_prev.clicked.connect(lambda: self._step_variant(-1))
        self._variant_label = QLabel()
        self._variant_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:{theme.FS_XS}px;background:transparent;"
        )
        self._variant_next = QPushButton("›")
        self._variant_next.setToolTip("Next alternative reply")
        self._variant_next.clicked.connect(lambda: self._step_variant(1))
        for btn in (self._variant_prev, self._variant_next):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(18, 20)
            btn.setStyleSheet(
                f"QPushButton{{background:transparent;color:{theme.FAINT};"
                f"border:none;font-size:{theme.FS_SM}px;}}"
                f"QPushButton:hover{{color:{theme.TEXT};}}"
                f"QPushButton:disabled{{color:{theme.BORDER_HI};}}"
            )
        for w in (self._variant_prev, self._variant_label, self._variant_next):
            w.hide()
            header_row.addSpacing(2)
            header_row.addWidget(w)

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

        self._branch_btn = self._make_action_btn(
            "⑂", "Branch: copy the chat up to here into a new one"
        )
        self._branch_btn.clicked.connect(self._emit_branch)
        header_row.addWidget(self._branch_btn)

        col.addLayout(header_row)

        # Attachment chips sit above the text, like every other chat client.
        self._attach_row = QHBoxLayout()
        self._attach_row.setContentsMargins(0, 2, 0, 2)
        self._attach_row.setSpacing(6)
        self._attach_wrap = QWidget()
        self._attach_wrap.setLayout(self._attach_row)
        self._attach_wrap.hide()
        col.addWidget(self._attach_wrap)

        self.browser = _AutoTextEdit()
        # Don't set `color` here: a widget-level colour overrides inline HTML colours
        # for un-tokenised text, which breaks dark code blocks under the light theme.
        # The document's body CSS drives the body text colour instead.
        self.browser.setStyleSheet(
            f"QTextBrowser {{ background:transparent; border:none; "
            f"font-size:{theme.FS_BASE}px; }}"
        )
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        col.addWidget(self.browser)

        if self.role == "assistant":
            self._token_label = QLabel()
            self._token_label.setStyleSheet(
                f"color:{theme.FAINT};font-size:{theme.FS_XS}px;background:transparent;"
            )
            self._token_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._token_label.hide()
            col.addWidget(self._token_label)
            self._tools_label = QLabel()
            self._tools_label.setStyleSheet(
                f"color:{theme.FAINT};font-size:{theme.FS_XS}px;"
                f"background:transparent;"
            )
            self._tools_label.hide()
            col.addWidget(self._tools_label)
        else:
            self._token_label = None
            self._tools_label = None

        outer.addLayout(col, stretch=1)

    def _make_action_btn(self, text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(22)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.FAINT};border:none;"
            f"font-size:{theme.FS_XS}px;padding:0 7px;border-radius:6px;}}"
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

    def _emit_branch(self):
        if self.message_id is not None:
            self.branch_requested.emit(self.message_id)

    def set_variants(self, ids: list[int], current_id: int):
        """Show the ‹ n/N › switcher when a reply has alternates."""
        self._variant_ids = list(ids or [])
        show = len(self._variant_ids) > 1 and current_id in self._variant_ids
        for w in (self._variant_prev, self._variant_label, self._variant_next):
            w.setVisible(show)
        if not show:
            return
        idx = self._variant_ids.index(current_id)
        self._variant_label.setText(f"{idx + 1}/{len(self._variant_ids)}")
        self._variant_prev.setEnabled(idx > 0)
        self._variant_next.setEnabled(idx < len(self._variant_ids) - 1)

    def _step_variant(self, delta: int):
        if self.message_id is None or self.message_id not in self._variant_ids:
            return
        idx = self._variant_ids.index(self.message_id) + delta
        if 0 <= idx < len(self._variant_ids):
            self.variant_requested.emit(self._variant_ids[idx])

    def set_tools_used(self, names: list[str]):
        if self._tools_label is None or not names:
            return
        unique = ", ".join(dict.fromkeys(names))
        self._tools_label.setText(f"🔧 tools used: {unique}")
        self._tools_label.show()

    def set_attachments(self, rows):
        """Render attachment chips: image thumbnails, file chips for text."""
        while self._attach_row.count():
            item = self._attach_row.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        rows = list(rows or [])
        if not rows:
            self._attach_wrap.hide()
            return
        for row in rows:
            self._attach_row.addWidget(self._make_chip(row))
        self._attach_row.addStretch()
        self._attach_wrap.show()

    def _make_chip(self, row) -> QWidget:
        name = row["name"]
        if row["kind"] == "image" and row["data"]:
            pix = QPixmap()
            if pix.loadFromData(bytes(row["data"])):
                label = QLabel()
                label.setPixmap(
                    pix.scaled(
                        THUMB_MAX, THUMB_MAX,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                label.setToolTip(name)
                label.setStyleSheet(
                    f"border:1px solid {theme.BORDER_HI};"
                    f"border-radius:{theme.RADIUS_SM}px;padding:2px;"
                )
                return label
        chip = QLabel(f"📄 {name}")
        chip.setToolTip(name)
        chip.setStyleSheet(
            f"color:{theme.MUTED};background:{theme.SURFACE_HI};"
            f"border:1px solid {theme.BORDER};border-radius:{theme.RADIUS_SM}px;"
            f"padding:3px 9px;font-size:{theme.FS_XS}px;"
        )
        return chip

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
        cost = pricing.estimate_cost(self._model, prompt_tokens, completion_tokens)
        if cost is not None:
            text += f"  ·  ${cost:.4f}"
        self._token_label.setText(text)
        self._token_label.show()

    def append_chunk(self, text: str):
        """Append streamed text, live-rendering Markdown if that is enabled."""
        self._raw_text += text
        if self._live_markdown:
            if len(self._raw_text) > LIVE_RENDER_MAX_CHARS:
                # Too long to keep re-laying out: fall back to plain appends and
                # resync the view once so nothing is lost in the switch.
                self._live_markdown = False
                self._render_timer.stop()
                self.browser.setPlainText(self._raw_text)
                return
            if not self._render_timer.isActive():
                self._render_timer.start(LIVE_RENDER_MS)
            return
        cursor = self.browser.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.browser.setTextCursor(cursor)

    def _live_render(self):
        """Render the settled part as Markdown, leaving the tail as plain text."""
        if not self._streaming:
            return
        head, tail = utils.stable_prefix(self._raw_text)
        body, self._code_blocks = render_markdown(head) if head else ("", [])
        if tail:
            body += (
                "<p>" + utils.escape_html(tail).replace("\n", "<br>") + "</p>"
            )
        self._set_html(body)

    def finalize(self):
        """Re-render with full Markdown once streaming is done."""
        self._render_timer.stop()
        self._streaming = False
        self._render(self._raw_text)
        self._update_actions_visibility()

    def get_text(self) -> str:
        return self._raw_text

    def _render(self, text: str):
        body, self._code_blocks = render_markdown(text)
        self._set_html(body)

    def _set_html(self, body: str):
        full = (
            f"<html><head><style>{_content_css()}</style></head>"
            f"<body>{body}</body></html>"
        )
        self.browser.setHtml(full)
