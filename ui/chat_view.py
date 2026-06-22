import sys
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QScrollArea, QLabel, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QTextCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import theme
from api_client import StreamWorker
from ui.message_widget import MessageWidget
from ui.settings_dialog import ChatOptionsDialog


class _InputEdit(QTextEdit):
    """QTextEdit that sends on Enter (Shift+Enter = newline) and grows with content.

    It starts compact and expands line-by-line as the user types, up to a cap, like
    modern chat composers.
    """

    send_triggered = pyqtSignal()

    MIN_H = 50
    MAX_H = 168

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.MIN_H)
        self.document().contentsChanged.connect(self._autosize)

    def _autosize(self):
        doc_h = self.document().size().height()
        # Add the stylesheet's vertical padding (~12px each side) + frame.
        target = int(doc_h) + 26
        h = max(self.MIN_H, min(target, self.MAX_H))
        if h != self.height():
            self.setFixedHeight(h)
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if target > self.MAX_H
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def keyPressEvent(self, event: QKeyEvent):
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        ):
            self.send_triggered.emit()
            return
        super().keyPressEvent(event)


class ChatView(QWidget):
    """Right panel: message history + input area."""

    chat_updated = pyqtSignal(int)   # emitted after assistant reply is saved
    status_updated = pyqtSignal(str) # emitted to update the main window status bar
    new_chat_requested = pyqtSignal() # emitted by the welcome screen's "New chat"

    def __init__(self, settings, db, memory=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.memory = memory
        self.current_chat_id: int | None = None
        self.stream_worker: StreamWorker | None = None
        self._stream_widget: MessageWidget | None = None
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._chat_overrides: dict = {}  # per-chat model/system_prompt/temperature
        self._setup_ui()


    def _setup_ui(self):
        self.setStyleSheet(f"background:{theme.BG};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.welcome = self._make_welcome()

        self.chat_header = self._make_chat_header()
        self.chat_header.hide()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            f"QScrollArea{{border:none;background:{theme.BG};}}"
            f"QScrollArea > QWidget > QWidget {{ background:{theme.BG}; }}"
        )

        # Messages live in a column centred to a comfortable reading width.
        self.msg_container = QWidget()
        cwrap = QHBoxLayout(self.msg_container)
        cwrap.setContentsMargins(0, 0, 0, 0)
        cwrap.setSpacing(0)
        cwrap.addStretch(1)
        self._msg_col = QWidget()
        self._msg_col.setMaximumWidth(theme.CONTENT_MAX_WIDTH)
        self.msg_layout = QVBoxLayout(self._msg_col)
        self.msg_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.msg_layout.setSpacing(14)
        self.msg_layout.setContentsMargins(8, 20, 8, 24)
        cwrap.addWidget(self._msg_col, stretch=20)
        cwrap.addStretch(1)

        self.scroll_area.setWidget(self.msg_container)
        self.scroll_area.hide()

        self.input_panel = self._make_input_panel()
        self.input_panel.hide()

        lay.addWidget(self.welcome, stretch=1)
        lay.addWidget(self.chat_header)
        lay.addWidget(self.scroll_area, stretch=1)
        lay.addWidget(self.input_panel)

    def _make_welcome(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background:{theme.BG};")
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(10)

        logo = QLabel("✦")
        logo.setFixedSize(72, 72)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setStyleSheet(
            f"background:{theme.SURFACE_SEL};color:{theme.ACCENT};"
            f"border-radius:36px;font-size:34px;"
        )
        v.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Welcome to PyQOA")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color:{theme.TEXT};font-size:22px;font-weight:700;background:transparent;"
        )
        v.addWidget(title)

        subtitle = QLabel("Select a conversation on the left, or start a new one.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            f"color:{theme.MUTED};font-size:14px;background:transparent;"
        )
        v.addWidget(subtitle)

        new_btn = QPushButton("＋  New chat")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setFixedHeight(40)
        new_btn.setMinimumWidth(150)
        new_btn.setStyleSheet(theme.primary_button_qss(theme.RADIUS_SM))
        new_btn.clicked.connect(self.new_chat_requested)
        v.addSpacing(6)
        v.addWidget(new_btn, alignment=Qt.AlignmentFlag.AlignCenter)
        return w

    def _make_chat_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet(
            f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 16, 0)
        lay.setSpacing(12)

        self._model_badge = QLabel()
        self._model_badge.setStyleSheet(
            f"color:{theme.MUTED};font-size:12px;font-weight:600;"
            f"background:{theme.SURFACE_HI};border:1px solid {theme.BORDER};"
            f"border-radius:11px;padding:3px 11px;"
        )
        lay.addWidget(self._model_badge)
        lay.addStretch()

        self._token_total_label = QLabel()
        self._token_total_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:11px;"
        )
        lay.addWidget(self._token_total_label)

        self._chat_opts_btn = QPushButton("⚙  Chat options")
        self._chat_opts_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._chat_opts_btn.setToolTip(
            "Override model / system prompt / temperature for this chat"
        )
        self._chat_opts_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.MUTED};border:none;"
            f"font-size:12px;padding:5px 10px;border-radius:8px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};background:{theme.SURFACE_HI};}}"
        )
        self._chat_opts_btn.clicked.connect(self._open_chat_options)
        lay.addWidget(self._chat_opts_btn)

        return bar

    def _make_input_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            f"background:{theme.PANEL};border-top:1px solid {theme.BORDER};"
        )
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(0, 14, 0, 16)
        outer.setSpacing(0)
        outer.addStretch(1)

        inner = QWidget()
        inner.setMaximumWidth(theme.CONTENT_MAX_WIDTH)
        lay = QHBoxLayout(inner)
        lay.setContentsMargins(8, 0, 8, 0)
        lay.setSpacing(10)

        self.input_edit = _InputEdit()
        self.input_edit.setPlaceholderText("Message…   (Enter to send · Shift+Enter for newline)")
        self.input_edit.setStyleSheet(f"""
            QTextEdit {{
                background:{theme.SURFACE}; color:{theme.TEXT};
                border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS}px;
                padding:12px 16px; font-size:14px;
            }}
            QTextEdit:focus {{ border:1px solid {theme.ACCENT}; }}
        """)
        self.input_edit.send_triggered.connect(self._send)

        self.send_btn = QPushButton("↑")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setToolTip("Send  (Enter)")
        self.send_btn.setFixedSize(46, 46)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.ACCENT}; color:white;
                border:none; border-radius:23px;
                font-size:22px; font-weight:700;
            }}
            QPushButton:hover  {{ background:{theme.ACCENT_HI}; }}
            QPushButton:pressed{{ background:{theme.ACCENT_DEEP}; }}
            QPushButton:disabled{{ background:{theme.SURFACE_HI}; color:{theme.FAINT}; }}
        """)
        self.send_btn.clicked.connect(self._send)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setToolTip("Stop generating")
        self.stop_btn.setFixedSize(46, 46)
        self.stop_btn.hide()
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.DANGER_BG}; color:{theme.DANGER};
                border:1px solid {theme.DANGER_BD}; border-radius:23px;
                font-size:15px; font-weight:700;
            }}
            QPushButton:hover {{ background:{theme.DANGER_BD};color:white; }}
        """)
        self.stop_btn.clicked.connect(self._stop_stream)

        lay.addWidget(self.input_edit)
        lay.addWidget(self.send_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        lay.addWidget(self.stop_btn, alignment=Qt.AlignmentFlag.AlignBottom)

        outer.addWidget(inner, stretch=20)
        outer.addStretch(1)
        return panel


    def load_chat(self, chat_id: int, force: bool = False):
        """Load and display all messages for chat_id."""
        if chat_id == self.current_chat_id and not force:
            return  # already showing this chat — avoid a redundant reload

        if self.stream_worker and self.stream_worker.isRunning():
            # Block the worker's signals first: cancelling still lets run() emit a
            # final `finished`, which would otherwise save a partial reply into the
            # chat we are switching to.
            self.stream_worker.blockSignals(True)
            self.stream_worker.cancel()
            self.stream_worker.wait()
            self._reset_stream_ui()

        self.current_chat_id = chat_id
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._load_overrides(chat_id)
        self._clear_messages()

        model = self._effective("model", "")
        self._model_badge.setText(model)

        rows = self.db.get_messages(chat_id)
        for row in rows:
            w = self._add_widget(
                row["role"], row["content"], model=model, message_id=row["id"]
            )
            pt = row["prompt_tokens"] if "prompt_tokens" in row.keys() else 0
            ct = row["completion_tokens"] if "completion_tokens" in row.keys() else 0
            if (pt or ct) and row["role"] == "assistant":
                w.set_token_info(pt, ct)

        self.welcome.hide()
        self.chat_header.show()
        self.scroll_area.show()
        self.input_panel.show()
        self.input_edit.setFocus()
        self._refresh_token_totals()
        QTimer.singleShot(50, self._scroll_bottom)


    def _refresh_token_totals(self):
        if self.current_chat_id is None:
            return
        model = self._effective("model", "")
        p, c = self.db.get_chat_token_totals(self.current_chat_id)
        total = p + c
        if total:
            self._token_total_label.setText(
                f"Chat: {total:,} tokens  ({p:,} prompt + {c:,} completion)"
            )
            self.status_updated.emit(
                f"Model: {model}  |  "
                f"Chat: {total:,} tokens  ({p:,} prompt + {c:,} completion)"
            )
        else:
            self._token_total_label.setText("")
            self.status_updated.emit(f"Model: {model}")

    def _clear_messages(self):
        while self.msg_layout.count():
            item = self.msg_layout.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        # Trailing expanding spacer: absorbs leftover vertical space so bubbles
        # hug their content and stack from the top instead of stretching.
        self.msg_layout.addStretch(1)

    def _add_widget(
        self,
        role: str,
        content: str = "",
        streaming: bool = False,
        model: str = "",
        message_id: int | None = None,
    ) -> MessageWidget:
        w = MessageWidget(
            role, content, streaming=streaming, model=model, message_id=message_id
        )
        w.edit_requested.connect(self._edit_from)
        w.regenerate_requested.connect(self._regenerate)
        # Insert before the trailing stretch (kept last by _clear_messages).
        insert_at = max(0, self.msg_layout.count() - 1)
        self.msg_layout.insertWidget(insert_at, w)
        QTimer.singleShot(80, self._scroll_bottom)
        return w

    def _scroll_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _load_overrides(self, chat_id: int):
        row = self.db.get_chat(chat_id)
        self._chat_overrides = {}
        if row is None:
            return
        keys = row.keys()
        for key in ("model", "system_prompt", "temperature"):
            val = row[key] if key in keys else None
            if val is not None and val != "":
                self._chat_overrides[key] = val

    def _effective(self, key: str, default=None):
        """Per-chat override (if set) falls back to the global setting."""
        if key in self._chat_overrides:
            return self._chat_overrides[key]
        return self.settings.get(key, default)

    def _worker_overrides(self) -> dict:
        """The subset of overrides the StreamWorker understands."""
        return {k: v for k, v in self._chat_overrides.items()
                if k in ("model", "temperature")}

    def _send(self):
        if not self.current_chat_id:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return

        self.input_edit.clear()
        mid = self.db.add_message(self.current_chat_id, "user", text)
        self._add_widget("user", text, message_id=mid)
        self._begin_stream(text)

    def _begin_stream(self, query: str):
        """Start streaming an assistant reply for the current chat history."""
        self.send_btn.setEnabled(False)
        self.stop_btn.show()

        model = self._effective("model", "")
        self._stream_widget = self._add_widget("assistant", streaming=True, model=model)

        if self.memory:
            self.stream_worker = StreamWorker(
                self.settings,
                memory=self.memory,
                chat_id=self.current_chat_id,
                current_query=query,
                system_prompt=self._effective("system_prompt", ""),
                overrides=self._worker_overrides(),
            )
        else:
            self.stream_worker = StreamWorker(
                self.settings,
                messages=self._build_api_messages(),
                overrides=self._worker_overrides(),
            )
        self.stream_worker.chunk_received.connect(self._on_chunk)
        self.stream_worker.finished.connect(self._on_finished)
        self.stream_worker.error.connect(self._on_error)
        self.stream_worker.usage_received.connect(self._on_usage_received)
        self.stream_worker.context_built.connect(self._on_context_built)
        self.stream_worker.start()

    def _regenerate(self, message_id: int):
        """Drop this assistant reply (and anything after) and generate a new one."""
        if not self.current_chat_id:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return
        self.db.delete_messages_from(self.current_chat_id, message_id)
        self.load_chat(self.current_chat_id, force=True)
        rows = self.db.get_messages(self.current_chat_id)
        last_user = next(
            (r["content"] for r in reversed(rows) if r["role"] == "user"), ""
        )
        if not last_user:
            return  # nothing left to answer
        self._begin_stream(last_user)

    def _edit_from(self, message_id: int):
        """Move a user message back into the input box; drop it and everything after."""
        if not self.current_chat_id:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return
        row = next(
            (r for r in self.db.get_messages(self.current_chat_id)
             if r["id"] == message_id),
            None,
        )
        if row is None:
            return
        self.db.delete_messages_from(self.current_chat_id, message_id)
        self.load_chat(self.current_chat_id, force=True)
        self.input_edit.setPlainText(row["content"])
        self.input_edit.setFocus()
        cursor = self.input_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.input_edit.setTextCursor(cursor)

    def _stop_stream(self):
        if self.stream_worker:
            self.stream_worker.cancel()

    def _build_api_messages(self) -> list:
        msgs = []
        sp = self._effective("system_prompt", "")
        if sp:
            msgs.append({"role": "system", "content": sp})
        for row in self.db.get_messages(self.current_chat_id):
            msgs.append({"role": row["role"], "content": row["content"]})
        return msgs

    def _on_chunk(self, text: str):
        if self._stream_widget:
            self._stream_widget.append_chunk(text)
            self._scroll_bottom()

    def _on_context_built(self, retrieved: int):
        if retrieved > 0:
            self.status_updated.emit(
                f"Recalled {retrieved} relevant earlier message(s) via vector memory"
            )

    def _on_usage_received(self, prompt_tokens: int, completion_tokens: int):
        self._pending_prompt_tokens = prompt_tokens
        self._pending_completion_tokens = completion_tokens
        if self._stream_widget:
            self._stream_widget.set_token_info(prompt_tokens, completion_tokens)

    def _on_finished(self, full_text: str):
        if self._stream_widget:
            self._stream_widget.finalize()

        if full_text:
            mid = self.db.add_message(
                self.current_chat_id,
                "assistant",
                full_text,
                self._pending_prompt_tokens,
                self._pending_completion_tokens,
            )
            if self._stream_widget:
                # Now persisted — reveal the Copy/Regenerate actions on this bubble.
                self._stream_widget.set_message_id(mid)

        rows = self.db.get_messages(self.current_chat_id)
        if len(rows) == 2:
            first = rows[0]["content"]
            title = (first[:47] + "…") if len(first) > 50 else first
            self.db.update_chat_title(self.current_chat_id, title)

        self._finish_stream()

    def _on_error(self, error: str):
        if self._stream_widget:
            self._stream_widget.append_chunk(f"\n\n⚠ Error: {error}")
            self._stream_widget.finalize()
        self._finish_stream()
        QMessageBox.critical(self, "API Error", error)

    def _reset_stream_ui(self):
        """Reset the input controls and streaming state (no DB/UI refresh)."""
        self.send_btn.setEnabled(True)
        self.stop_btn.hide()
        self._stream_widget = None
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0

    def _finish_stream(self):
        self._reset_stream_ui()
        self._refresh_token_totals()
        self.chat_updated.emit(self.current_chat_id)
        QTimer.singleShot(80, self._scroll_bottom)

    def _open_chat_options(self):
        if not self.current_chat_id:
            return
        dlg = ChatOptionsDialog(self.settings, dict(self._chat_overrides), self)
        if dlg.exec():
            ov = dlg.result_overrides()
            self.db.update_chat_overrides(
                self.current_chat_id,
                ov.get("model"),
                ov.get("system_prompt"),
                ov.get("temperature"),
            )
            self._load_overrides(self.current_chat_id)
            self._model_badge.setText(self._effective("model", ""))
            self._refresh_token_totals()
