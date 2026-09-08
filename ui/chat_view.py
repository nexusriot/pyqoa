import os
import sys

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton,
    QScrollArea, QLabel, QSizePolicy, QMessageBox, QFileDialog, QLineEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QTextCursor, QShortcut, QKeySequence

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import attachments as attach_mod
import theme
import tokens as token_counter
from api_client import StreamWorker, TitleWorker, fallback_title, preflight
from ui.message_widget import MessageWidget
from ui.settings_dialog import ChatOptionsDialog, PromptLibraryDialog

TOKEN_METER_DEBOUNCE_MS = 400


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

    chat_updated = pyqtSignal(int)    # emitted after assistant reply is saved
    status_updated = pyqtSignal(str)  # emitted to update the main window status bar
    new_chat_requested = pyqtSignal() # emitted by the welcome screen's "New chat"
    settings_requested = pyqtSignal() # emitted from the error banner's Settings link
    chat_branched = pyqtSignal(int)   # emitted with the new chat id after a branch

    def __init__(self, settings, db, memory=None, registry=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.db = db
        self.memory = memory
        self.registry = registry
        self.current_chat_id: int | None = None
        self.stream_worker: StreamWorker | None = None
        self._title_worker: TitleWorker | None = None
        self._stream_widget: MessageWidget | None = None
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._pending_variant_group: int | None = None
        self._pending_attachments: list[dict] = []
        self._chat_overrides: dict = {}  # per-chat model/system_prompt/temperature
        self._message_widgets: list[MessageWidget] = []
        self._context_tokens = 0
        self._find_matches: list[int] = []
        self._find_index = -1
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet(f"background:{theme.BG};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.welcome = self._make_welcome()

        self.chat_header = self._make_chat_header()
        self.chat_header.hide()

        self.find_bar = self._make_find_bar()
        self.find_bar.hide()

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

        self.error_banner = self._make_error_banner()
        self.error_banner.hide()

        self.input_panel = self._make_input_panel()
        self.input_panel.hide()

        lay.addWidget(self.welcome, stretch=1)
        lay.addWidget(self.chat_header)
        lay.addWidget(self.find_bar)
        lay.addWidget(self.scroll_area, stretch=1)
        lay.addWidget(self.error_banner)
        lay.addWidget(self.input_panel)

        # Ctrl+F lives on the View menu action (one owner, or Qt reports an
        # ambiguous shortcut); Escape is scoped to this panel.
        escape = QShortcut(QKeySequence("Escape"), self, self._hide_find)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

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
            f"border-radius:36px;font-size:{theme.FS_LOGO}px;"
        )
        v.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        title = QLabel("Welcome to PyQOA")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            f"color:{theme.TEXT};font-size:{theme.FS_TITLE}px;font-weight:700;"
            f"background:transparent;"
        )
        v.addWidget(title)

        subtitle = QLabel("Select a conversation on the left, or start a new one.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(
            f"color:{theme.MUTED};font-size:{theme.FS_BASE}px;background:transparent;"
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

    def _header_button(self, text: str, tooltip: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.MUTED};border:none;"
            f"font-size:{theme.FS_SM}px;padding:5px 10px;border-radius:8px;}}"
            f"QPushButton:hover{{color:{theme.TEXT};background:{theme.SURFACE_HI};}}"
        )
        return btn

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
            f"color:{theme.MUTED};font-size:{theme.FS_SM}px;font-weight:600;"
            f"background:{theme.SURFACE_HI};border:1px solid {theme.BORDER};"
            f"border-radius:11px;padding:3px 11px;"
        )
        lay.addWidget(self._model_badge)
        lay.addStretch()

        self._token_total_label = QLabel()
        self._token_total_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:{theme.FS_XS}px;"
        )
        lay.addWidget(self._token_total_label)

        find_btn = self._header_button("🔍", "Find in this chat  (Ctrl+F)")
        find_btn.clicked.connect(self.toggle_find)
        lay.addWidget(find_btn)

        self._chat_opts_btn = self._header_button(
            "⚙  Chat options",
            "Override model / system prompt / temperature for this chat",
        )
        self._chat_opts_btn.clicked.connect(self._open_chat_options)
        lay.addWidget(self._chat_opts_btn)

        return bar

    def _make_find_bar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(40)
        bar.setStyleSheet(
            f"background:{theme.PANEL};border-bottom:1px solid {theme.BORDER};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 4, 16, 4)
        lay.setSpacing(8)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find in conversation…")
        self.find_edit.setStyleSheet(
            f"QLineEdit{{background:{theme.SURFACE};color:{theme.TEXT};"
            f"border:1px solid {theme.BORDER};border-radius:{theme.RADIUS_SM}px;"
            f"padding:4px 10px;font-size:{theme.FS_MD}px;}}"
            f"QLineEdit:focus{{border:1px solid {theme.ACCENT};}}"
        )
        self.find_edit.textChanged.connect(self._run_find)
        self.find_edit.returnPressed.connect(lambda: self._step_find(1))
        lay.addWidget(self.find_edit, stretch=1)

        self._find_status = QLabel()
        self._find_status.setStyleSheet(
            f"color:{theme.FAINT};font-size:{theme.FS_XS}px;"
        )
        lay.addWidget(self._find_status)

        for label, delta in (("‹", -1), ("›", 1)):
            btn = self._header_button(label, "Previous match" if delta < 0 else "Next match")
            btn.clicked.connect(lambda _=False, d=delta: self._step_find(d))
            lay.addWidget(btn)
        close_btn = self._header_button("✕", "Close find bar  (Esc)")
        close_btn.clicked.connect(self._hide_find)
        lay.addWidget(close_btn)
        return bar

    def _make_error_banner(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(
            f"background:{theme.DANGER_BG};border-top:1px solid {theme.DANGER_BD};"
        )
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 8, 16, 8)
        lay.setSpacing(12)
        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet(
            f"color:{theme.DANGER};font-size:{theme.FS_SM}px;background:transparent;"
        )
        lay.addWidget(self._error_label, stretch=1)

        settings_btn = QPushButton("Open Settings")
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.DANGER};"
            f"border:1px solid {theme.DANGER_BD};border-radius:{theme.RADIUS_SM}px;"
            f"padding:4px 12px;font-size:{theme.FS_SM}px;}}"
            f"QPushButton:hover{{background:{theme.DANGER_BD};color:white;}}"
        )
        settings_btn.clicked.connect(self.settings_requested)
        lay.addWidget(settings_btn)

        dismiss = QPushButton("✕")
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setFixedWidth(28)
        dismiss.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.DANGER};border:none;"
            f"font-size:{theme.FS_SM}px;}}"
        )
        dismiss.clicked.connect(bar.hide)
        lay.addWidget(dismiss)
        return bar

    def _make_input_panel(self) -> QWidget:
        panel = QWidget()
        panel.setStyleSheet(
            f"background:{theme.PANEL};border-top:1px solid {theme.BORDER};"
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 8, 0, 10)
        outer.setSpacing(4)

        # Pending attachment chips, shown only once something is attached.
        self._pending_row = QHBoxLayout()
        self._pending_row.setContentsMargins(0, 0, 0, 0)
        self._pending_row.setSpacing(6)
        self._pending_wrap = self._centered(self._pending_row)
        self._pending_wrap.hide()
        outer.addWidget(self._pending_wrap)

        row = QHBoxLayout()
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(10)

        self.attach_btn = QPushButton("📎")
        self.attach_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_btn.setToolTip("Attach an image or text file")
        self.attach_btn.setFixedSize(38, 38)
        self.attach_btn.setStyleSheet(
            f"QPushButton{{background:{theme.SURFACE};color:{theme.MUTED};"
            f"border:1px solid {theme.BORDER};border-radius:19px;"
            f"font-size:{theme.FS_ACTION}px;}}"
            f"QPushButton:hover{{background:{theme.SURFACE_HI};color:{theme.TEXT};}}"
        )
        self.attach_btn.clicked.connect(self._pick_attachments)

        self.input_edit = _InputEdit()
        self.input_edit.setPlaceholderText(
            "Message…   (Enter to send · Shift+Enter for newline)"
        )
        self.input_edit.setStyleSheet(f"""
            QTextEdit {{
                background:{theme.SURFACE}; color:{theme.TEXT};
                border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS}px;
                padding:12px 16px; font-size:{theme.FS_BASE}px;
            }}
            QTextEdit:focus {{ border:1px solid {theme.ACCENT}; }}
        """)
        self.input_edit.send_triggered.connect(self._send)
        self.input_edit.textChanged.connect(self._schedule_token_meter)

        self.send_btn = QPushButton("↑")
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setToolTip("Send  (Enter)")
        self.send_btn.setFixedSize(46, 46)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.ACCENT}; color:white;
                border:none; border-radius:23px;
                font-size:{theme.FS_TITLE}px; font-weight:700;
            }}
            QPushButton:hover  {{ background:{theme.ACCENT_HI}; }}
            QPushButton:pressed{{ background:{theme.ACCENT_DEEP}; }}
            QPushButton:disabled{{ background:{theme.SURFACE_HI}; color:{theme.FAINT}; }}
        """)
        self.send_btn.clicked.connect(self._send)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.stop_btn.setToolTip("Stop generating (the partial reply is kept)")
        self.stop_btn.setFixedSize(46, 46)
        self.stop_btn.hide()
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.DANGER_BG}; color:{theme.DANGER};
                border:1px solid {theme.DANGER_BD}; border-radius:23px;
                font-size:{theme.FS_ACTION}px; font-weight:700;
            }}
            QPushButton:hover {{ background:{theme.DANGER_BD};color:white; }}
        """)
        self.stop_btn.clicked.connect(self._stop_stream)

        row.addWidget(self.attach_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.input_edit)
        row.addWidget(self.send_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        row.addWidget(self.stop_btn, alignment=Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(self._centered(row))

        meter_row = QHBoxLayout()
        meter_row.setContentsMargins(10, 0, 10, 0)
        prompts_btn = self._header_button("✎ Prompts", "Insert a saved prompt")
        prompts_btn.clicked.connect(self._open_prompt_library)
        meter_row.addWidget(prompts_btn)
        meter_row.addStretch()
        self._meter_label = QLabel()
        self._meter_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:{theme.FS_XS}px;background:transparent;"
        )
        meter_row.addWidget(self._meter_label)
        outer.addWidget(self._centered(meter_row))

        self._meter_timer = QTimer(self)
        self._meter_timer.setSingleShot(True)
        self._meter_timer.timeout.connect(self._update_token_meter)
        return panel

    def _centered(self, inner_layout) -> QWidget:
        """Wrap a layout in the centred, width-capped reading column."""
        wrap = QWidget()
        outer = QHBoxLayout(wrap)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addStretch(1)
        inner = QWidget()
        inner.setMaximumWidth(theme.CONTENT_MAX_WIDTH)
        inner.setLayout(inner_layout)
        outer.addWidget(inner, stretch=20)
        outer.addStretch(1)
        return wrap

    def load_chat(
        self,
        chat_id: int,
        force: bool = False,
        focus_message_id: int | None = None,
    ):
        """Load and display all messages for chat_id."""
        if chat_id == self.current_chat_id and not force:
            if focus_message_id is not None:
                self._scroll_to_message(focus_message_id)
            return  # already showing this chat — avoid a redundant reload

        if self.stream_worker and self.stream_worker.isRunning():
            # Block the worker's signals first: cancelling still lets run() emit a
            # final `completed`, which would otherwise save a partial reply into
            # the chat we are switching to.
            self.stream_worker.blockSignals(True)
            self.stream_worker.cancel()
            self.stream_worker.wait()
            self._reset_stream_ui()

        self.current_chat_id = chat_id
        self._pending_prompt_tokens = 0
        self._pending_completion_tokens = 0
        self._pending_variant_group = None
        self._clear_pending_attachments()
        self._load_overrides(chat_id)
        self._clear_messages()
        self.error_banner.hide()

        model = self._effective("model", "")
        self._model_badge.setText(model)

        rows = self.db.get_messages(chat_id)
        for row in rows:
            w = self._add_widget(
                row["role"],
                row["content"],
                model=row["model"] or model,
                message_id=row["id"],
            )
            pt = row["prompt_tokens"] if "prompt_tokens" in row.keys() else 0
            ct = row["completion_tokens"] if "completion_tokens" in row.keys() else 0
            if (pt or ct) and row["role"] == "assistant":
                w.set_token_info(pt, ct)
            w.set_attachments(self.db.get_attachments(row["id"]))
            if row["variant_group"] is not None:
                variants = self.db.get_variants(row["id"])
                w.set_variants([v["id"] for v in variants], row["id"])

        self.welcome.hide()
        self.chat_header.show()
        self.scroll_area.show()
        self.input_panel.show()
        self.input_edit.setFocus()
        self._refresh_token_totals()
        self._refresh_context_tokens()
        self._run_find()
        if focus_message_id is not None:
            QTimer.singleShot(80, lambda: self._scroll_to_message(focus_message_id))
        else:
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
        self._message_widgets.clear()
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
            role, content, streaming=streaming, model=model, message_id=message_id,
            live_markdown=bool(self.settings.get("stream_render", True)),
        )
        w.edit_requested.connect(self._edit_from)
        w.regenerate_requested.connect(self._regenerate)
        w.branch_requested.connect(self._branch)
        w.variant_requested.connect(self._switch_variant)
        # Insert before the trailing stretch (kept last by _clear_messages).
        insert_at = max(0, self.msg_layout.count() - 1)
        self.msg_layout.insertWidget(insert_at, w)
        self._message_widgets.append(w)
        QTimer.singleShot(80, self._scroll_bottom)
        return w

    def _scroll_bottom(self):
        bar = self.scroll_area.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _scroll_to_message(self, message_id: int):
        for w in self._message_widgets:
            if w.message_id == message_id:
                self.scroll_area.ensureWidgetVisible(w, 0, 60)
                self._flash(w)
                return

    def _flash(self, widget: MessageWidget):
        """Briefly outline a message so a jump target is obvious."""
        original = widget.styleSheet()
        widget.setStyleSheet(
            original + f"MessageWidget{{border:1px solid {theme.ACCENT};}}"
        )
        QTimer.singleShot(1200, lambda: widget.setStyleSheet(original))

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

    def _pick_attachments(self):
        if not self.settings.get("attachments_enabled", True):
            QMessageBox.information(
                self, "Attachments disabled",
                "Enable attachments in Settings to send images and files.",
            )
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Attach files", "", attach_mod.FILE_FILTER
        )
        for path in paths:
            try:
                self._pending_attachments.append(attach_mod.read_attachment(path))
            except ValueError as exc:
                QMessageBox.warning(self, "Cannot attach", str(exc))
        self._refresh_pending_attachments()

    def _clear_pending_attachments(self):
        self._pending_attachments.clear()
        self._refresh_pending_attachments()

    def _refresh_pending_attachments(self):
        while self._pending_row.count():
            item = self._pending_row.takeAt(0)
            if w := item.widget():
                w.deleteLater()
        if not self._pending_attachments:
            self._pending_wrap.hide()
            self._schedule_token_meter()
            return
        for index, item in enumerate(self._pending_attachments):
            chip = QPushButton(f"{item['name']}  ✕")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip("Remove this attachment")
            chip.setStyleSheet(
                f"QPushButton{{background:{theme.SURFACE_HI};color:{theme.MUTED};"
                f"border:1px solid {theme.BORDER};"
                f"border-radius:{theme.RADIUS_SM}px;padding:3px 9px;"
                f"font-size:{theme.FS_XS}px;}}"
                f"QPushButton:hover{{color:{theme.DANGER};"
                f"border-color:{theme.DANGER_BD};}}"
            )
            chip.clicked.connect(lambda _=False, i=index: self._drop_attachment(i))
            self._pending_row.addWidget(chip)
        self._pending_row.addStretch()
        self._pending_wrap.show()
        self._schedule_token_meter()

    def _drop_attachment(self, index: int):
        if 0 <= index < len(self._pending_attachments):
            self._pending_attachments.pop(index)
        self._refresh_pending_attachments()

    def _schedule_token_meter(self):
        self._meter_timer.start(TOKEN_METER_DEBOUNCE_MS)

    def _refresh_context_tokens(self):
        """Recompute the (expensive) conversation part of the token meter."""
        if self.current_chat_id is None or self.memory is None:
            self._context_tokens = 0
        else:
            try:
                self._context_tokens = self.memory.estimate_tokens(
                    self.current_chat_id,
                    system_prompt=self._effective("system_prompt", ""),
                )
            except Exception:
                self._context_tokens = 0
        self._update_token_meter()

    def _update_token_meter(self):
        draft = self.input_edit.toPlainText()
        model = self._effective("model", "")
        total = self._context_tokens + token_counter.count_tokens(draft, model)
        total += sum(
            800 if a["kind"] == attach_mod.KIND_IMAGE
            else token_counter.count_tokens(
                (a.get("data") or b"").decode("utf-8", "replace"), model
            )
            for a in self._pending_attachments
        )
        prefix = "" if token_counter.exact_counting_available() else "~"
        text = f"{prefix}{total:,} tokens in context"
        budget = 0
        try:
            budget = int(self.settings.get("memory_max_tokens", 0) or 0)
        except (TypeError, ValueError):
            budget = 0
        if budget:
            text += f" / {budget:,} budget"
            if total > budget:
                text += "  ·  older messages will be dropped"
        self._meter_label.setText(text)

    def toggle_find(self):
        if self.find_bar.isVisible():
            self._hide_find()
        elif self.current_chat_id is not None:
            self.find_bar.show()
            self.find_edit.setFocus()
            self.find_edit.selectAll()

    def _hide_find(self):
        self.find_bar.hide()
        self.input_edit.setFocus()

    def _run_find(self):
        query = self.find_edit.text().strip().lower() if hasattr(self, "find_edit") else ""
        self._find_matches = []
        self._find_index = -1
        if query:
            self._find_matches = [
                i for i, w in enumerate(self._message_widgets)
                if query in w.get_text().lower()
            ]
        self._update_find_status()
        if self._find_matches:
            self._step_find(1)

    def _step_find(self, delta: int):
        if not self._find_matches:
            return
        self._find_index = (self._find_index + delta) % len(self._find_matches)
        widget = self._message_widgets[self._find_matches[self._find_index]]
        self.scroll_area.ensureWidgetVisible(widget, 0, 60)
        self._flash(widget)
        self._update_find_status()

    def _update_find_status(self):
        if not self.find_edit.text().strip():
            self._find_status.setText("")
        elif not self._find_matches:
            self._find_status.setText("no matches")
        else:
            position = self._find_index + 1 if self._find_index >= 0 else 0
            self._find_status.setText(f"{position}/{len(self._find_matches)}")

    def _send(self):
        if not self.current_chat_id:
            return
        text = self.input_edit.toPlainText().strip()
        if not text and not self._pending_attachments:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return

        problem = preflight(self.settings, self._worker_overrides())
        if problem:
            self._show_error(problem)
            return
        self.error_banner.hide()

        self.input_edit.clear()
        mid = self.db.add_message(self.current_chat_id, "user", text)
        if self._pending_attachments:
            attach_mod.save_attachments(
                self.db, mid, self._pending_attachments
            )
        widget = self._add_widget("user", text, message_id=mid)
        widget.set_attachments(self.db.get_attachments(mid))
        self._clear_pending_attachments()
        self._begin_stream(text)

    def _begin_stream(self, query: str, variant_group: int | None = None):
        """Start streaming an assistant reply for the current chat history."""
        self.send_btn.setEnabled(False)
        self.stop_btn.show()
        self._pending_variant_group = variant_group

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
                registry=self.registry,
            )
        else:
            self.stream_worker = StreamWorker(
                self.settings,
                messages=self._build_api_messages(),
                overrides=self._worker_overrides(),
                registry=self.registry,
            )
        self.stream_worker.chunk_received.connect(self._on_chunk)
        self.stream_worker.completed.connect(self._on_completed)
        self.stream_worker.error.connect(self._on_error)
        self.stream_worker.usage_received.connect(self._on_usage_received)
        self.stream_worker.context_built.connect(self._on_context_built)
        self.stream_worker.tool_activity.connect(self.status_updated)
        self.stream_worker.retrying.connect(self._on_retrying)
        self.stream_worker.start()

    def _regenerate(self, message_id: int):
        """Keep this reply as an alternative and generate a fresh one beside it."""
        if not self.current_chat_id:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return
        removed = self.db.truncate_after(self.current_chat_id, message_id)
        self._forget(removed)
        group = self.db.begin_variant(message_id)
        self.load_chat(self.current_chat_id, force=True)
        rows = self.db.get_messages(self.current_chat_id)
        last_user = next(
            (r["content"] for r in reversed(rows) if r["role"] == "user"), ""
        )
        if not last_user:
            return  # nothing left to answer
        self._begin_stream(last_user, variant_group=group)

    def _switch_variant(self, message_id: int):
        if not self.current_chat_id:
            return
        if self.db.set_active_variant(message_id):
            self.load_chat(self.current_chat_id, force=True,
                           focus_message_id=message_id)

    def _branch(self, message_id: int):
        """Fork the conversation into a new chat ending at this message."""
        if not self.current_chat_id:
            return
        new_id = self.db.branch_chat(self.current_chat_id, message_id)
        self.status_updated.emit("Branched into a new chat.")
        self.chat_branched.emit(new_id)

    def _edit_from(self, message_id: int):
        """Move a user message back into the input box; drop it and everything after."""
        if not self.current_chat_id:
            return
        if self.stream_worker and self.stream_worker.isRunning():
            return
        row = self.db.get_message(message_id)
        if row is None:
            return
        removed = self.db.delete_messages_from(self.current_chat_id, message_id)
        self._forget(removed)
        self.load_chat(self.current_chat_id, force=True)
        self.input_edit.setPlainText(row["content"])
        self.input_edit.setFocus()
        cursor = self.input_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.input_edit.setTextCursor(cursor)

    def _forget(self, message_ids: list[int]):
        """Drop deleted messages from the vector index so they stop being recalled."""
        if self.memory and message_ids and self.current_chat_id is not None:
            self.memory.forget_messages(self.current_chat_id, message_ids)

    def _stop_stream(self):
        if self.stream_worker:
            self.status_updated.emit("Stopping — the partial reply will be kept.")
            self.stream_worker.cancel()

    def _build_api_messages(self) -> list:
        msgs = []
        sp = self._effective("system_prompt", "")
        if sp:
            msgs.append({"role": "system", "content": sp})
        for row in self.db.get_messages(self.current_chat_id):
            msgs.append(attach_mod.message_payload(self.db, row))
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

    def _on_retrying(self, attempt: int, total: int):
        self.status_updated.emit(f"Request failed — retrying ({attempt}/{total})…")

    def _on_usage_received(self, prompt_tokens: int, completion_tokens: int):
        self._pending_prompt_tokens = prompt_tokens
        self._pending_completion_tokens = completion_tokens
        if self._stream_widget:
            self._stream_widget.set_token_info(prompt_tokens, completion_tokens)

    def _on_completed(self, full_text: str):
        worker = self.stream_worker
        if self._stream_widget:
            self._stream_widget.finalize()
            if worker is not None and worker.tool_log:
                self._stream_widget.set_tools_used(worker.tool_log)

        if full_text:
            mid = self.db.add_message(
                self.current_chat_id,
                "assistant",
                full_text,
                self._pending_prompt_tokens,
                self._pending_completion_tokens,
                model=self._effective("model", ""),
                variant_group=self._pending_variant_group,
            )
            if self._stream_widget:
                # Now persisted — reveal the Copy/Regenerate actions on this bubble.
                self._stream_widget.set_message_id(mid)
                if self._pending_variant_group is not None:
                    variants = self.db.get_variants(mid)
                    self._stream_widget.set_variants(
                        [v["id"] for v in variants], mid
                    )
        self._pending_variant_group = None
        self._maybe_title()
        self._finish_stream()

    def _maybe_title(self):
        """Give a new chat a title: instant fallback, model-written if enabled."""
        chat = self.db.get_chat(self.current_chat_id)
        if chat is None or chat["title"] not in ("New Chat", ""):
            return
        rows = self.db.get_messages(self.current_chat_id)
        first_user = next((r["content"] for r in rows if r["role"] == "user"), "")
        if not first_user:
            return
        self.db.update_chat_title(self.current_chat_id, fallback_title(first_user))
        if not self.settings.get("auto_title", True):
            return
        if self._title_worker and self._title_worker.isRunning():
            return
        self._title_worker = TitleWorker(
            self.settings,
            self.current_chat_id,
            first_user,
            self.settings.get("auto_title_model", "") or self._effective("model", ""),
        )
        self._title_worker.title_ready.connect(self._on_title_ready)
        self._title_worker.start()

    def _on_title_ready(self, chat_id: int, title: str):
        self.db.update_chat_title(chat_id, title)
        self.chat_updated.emit(chat_id)

    def _on_error(self, error: str):
        self._show_error(error)
        if self._stream_widget:
            self._stream_widget.finalize()
        self._finish_stream()

    def _show_error(self, message: str):
        self._error_label.setText(message)
        self.error_banner.show()
        self.status_updated.emit(message.splitlines()[0])

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
        self._refresh_context_tokens()
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
            self._refresh_context_tokens()

    def _open_prompt_library(self):
        dlg = PromptLibraryDialog(self.settings, self)
        dlg.prompt_chosen.connect(self._insert_prompt)
        dlg.exec()

    def _insert_prompt(self, text: str):
        cursor = self.input_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        existing = self.input_edit.toPlainText()
        cursor.insertText(("\n\n" if existing.strip() else "") + text)
        self.input_edit.setTextCursor(cursor)
        self.input_edit.setFocus()
