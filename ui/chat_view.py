import sys
import os

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QScrollArea, QLabel, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import StreamWorker
from ui.message_widget import MessageWidget


class _InputEdit(QTextEdit):
    """QTextEdit that sends on Enter (plain Enter = send, Shift+Enter = newline)."""

    send_triggered = pyqtSignal()

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
        self._setup_ui()


    def _setup_ui(self):
        self.setStyleSheet("background:#212121;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.welcome = QLabel("Select a chat or create a new one  (Ctrl+N)")
        self.welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.welcome.setStyleSheet("color:#4b5563;font-size:16px;background:#212121;")
        self.welcome.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.chat_header = self._make_chat_header()
        self.chat_header.hide()

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet("QScrollArea{border:none;background:#212121;}")

        self.msg_container = QWidget()
        self.msg_container.setStyleSheet("background:#212121;")
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.msg_layout.setSpacing(10)
        self.msg_layout.setContentsMargins(20, 16, 20, 16)

        self.scroll_area.setWidget(self.msg_container)
        self.scroll_area.hide()

        self.input_panel = self._make_input_panel()
        self.input_panel.hide()

        lay.addWidget(self.welcome, stretch=1)
        lay.addWidget(self.chat_header)
        lay.addWidget(self.scroll_area, stretch=1)
        lay.addWidget(self.input_panel)

    def _make_chat_header(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet("background:#0f172a;border-bottom:1px solid #1e293b;")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(12)

        self._model_badge = QLabel()
        self._model_badge.setStyleSheet(
            "color:#94a3b8;font-size:12px;background:#1e293b;"
            "border-radius:4px;padding:2px 8px;"
        )
        lay.addWidget(self._model_badge)
        lay.addStretch()

        self._token_total_label = QLabel()
        self._token_total_label.setStyleSheet("color:#4b5563;font-size:11px;")
        lay.addWidget(self._token_total_label)

        return bar

    def _make_input_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            "background:#161b2e;border-top:1px solid #1e293b;"
        )
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        self.input_edit = _InputEdit()
        self.input_edit.setPlaceholderText("Message…  (Shift+Enter for newline)")
        self.input_edit.setMinimumHeight(48)
        self.input_edit.setMaximumHeight(140)
        self.input_edit.setStyleSheet("""
            QTextEdit {
                background:#1e293b; color:#ececf1;
                border:1px solid #334155; border-radius:10px;
                padding:10px 14px; font-size:14px;
            }
            QTextEdit:focus { border-color:#3b82f6; }
        """)
        self.input_edit.send_triggered.connect(self._send)

        self.send_btn = QPushButton("Send")
        self.send_btn.setFixedSize(82, 44)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background:#2563eb; color:white;
                border:none; border-radius:10px;
                font-size:14px; font-weight:bold;
            }
            QPushButton:hover  { background:#1d4ed8; }
            QPushButton:pressed{ background:#1e40af; }
            QPushButton:disabled{ background:#1e293b; color:#4b5563; }
        """)
        self.send_btn.clicked.connect(self._send)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedSize(72, 44)
        self.stop_btn.hide()
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background:#450a0a; color:#fca5a5;
                border:1px solid #7f1d1d; border-radius:10px;
                font-size:14px; font-weight:bold;
            }
            QPushButton:hover { background:#7f1d1d; }
        """)
        self.stop_btn.clicked.connect(self._stop_stream)

        lay.addWidget(self.input_edit)
        lay.addWidget(self.send_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        lay.addWidget(self.stop_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        return panel


    def load_chat(self, chat_id: int):
        """Load and display all messages for chat_id."""
        if self.stream_worker and self.stream_worker.isRunning():
            self.stream_worker.cancel()
            self.stream_worker.wait()

        self.current_chat_id = chat_id
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._clear_messages()

        model = self.settings.get("model", "")
        self._model_badge.setText(model)

        rows = self.db.get_messages(chat_id)
        for row in rows:
            w = self._add_widget(row["role"], row["content"], model=model)
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
        p, c = self.db.get_chat_token_totals(self.current_chat_id)
        total = p + c
        if total:
            self._token_total_label.setText(
                f"Chat: {total:,} tokens  ({p:,} prompt + {c:,} completion)"
            )
            self.status_updated.emit(
                f"Model: {self.settings.get('model', '')}  |  "
                f"Chat: {total:,} tokens  ({p:,} prompt + {c:,} completion)"
            )
        else:
            self._token_total_label.setText("")
            self.status_updated.emit(f"Model: {self.settings.get('model', '')}")

    def _clear_messages(self):
        while self.msg_layout.count():
            item = self.msg_layout.takeAt(0)
            if w := item.widget():
                w.deleteLater()

    def _add_widget(
        self, role: str, content: str = "", streaming: bool = False, model: str = ""
    ) -> MessageWidget:
        w = MessageWidget(role, content, streaming=streaming, model=model)
        self.msg_layout.addWidget(w)
        QTimer.singleShot(80, self._scroll_bottom)
        return w

    def _scroll_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _send(self):
        if not self.current_chat_id:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return

        self.input_edit.clear()
        self.send_btn.setEnabled(False)
        self.stop_btn.show()

        self.db.add_message(self.current_chat_id, "user", text)
        self._add_widget("user", text)

        model = self.settings.get("model", "")
        self._stream_widget = self._add_widget("assistant", streaming=True, model=model)

        if self.memory:
            self.stream_worker = StreamWorker(
                self.settings,
                memory=self.memory,
                chat_id=self.current_chat_id,
                current_query=text,
                system_prompt=self.settings.get("system_prompt", ""),
            )
        else:
            self.stream_worker = StreamWorker(
                self.settings, messages=self._build_api_messages()
            )
        self.stream_worker.chunk_received.connect(self._on_chunk)
        self.stream_worker.finished.connect(self._on_finished)
        self.stream_worker.error.connect(self._on_error)
        self.stream_worker.usage_received.connect(self._on_usage_received)
        self.stream_worker.context_built.connect(self._on_context_built)
        self.stream_worker.start()

    def _stop_stream(self):
        if self.stream_worker:
            self.stream_worker.cancel()

    def _build_api_messages(self) -> list:
        msgs = []
        sp = self.settings.get("system_prompt", "")
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
            self.db.add_message(
                self.current_chat_id,
                "assistant",
                full_text,
                self._pending_prompt_tokens,
                self._pending_completion_tokens,
            )

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

    def _finish_stream(self):
        self.send_btn.setEnabled(True)
        self.stop_btn.hide()
        self._stream_widget = None
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._refresh_token_totals()
        self.chat_updated.emit(self.current_chat_id)
        QTimer.singleShot(80, self._scroll_bottom)
