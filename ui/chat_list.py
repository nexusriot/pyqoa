import os
import sys
from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QMessageBox,
    QLineEdit, QFileDialog, QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_io
import theme

ROLE_CHAT_ID = Qt.ItemDataRole.UserRole
ROLE_MESSAGE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_KIND = Qt.ItemDataRole.UserRole + 2

KIND_CHAT = "chat"
KIND_MESSAGE = "message"
KIND_HEADER = "header"

SEARCH_DEBOUNCE_MS = 220
NO_FOLDER = "Chats"


class _ChatItemWidget(QWidget):
    """Custom widget rendered inside each QListWidgetItem."""

    def __init__(
        self,
        chat_id: int,
        title: str,
        updated_at: str,
        pinned: bool = False,
        archived: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.chat_id = chat_id
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        marks = ("📌 " if pinned else "") + ("🗄 " if archived else "")
        self.title_label = QLabel(marks + title)
        self.title_label.setStyleSheet(
            f"color:{theme.TEXT};font-size:{theme.FS_MD}px;font-weight:500;"
            f"background:transparent;"
        )
        self.title_label.setWordWrap(False)

        date_label = QLabel(self._fmt(updated_at))
        date_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:{theme.FS_XS}px;background:transparent;"
        )

        lay.addWidget(self.title_label)
        lay.addWidget(date_label)

    @staticmethod
    def _fmt(dt_str: str) -> str:
        try:
            # SQLite's datetime('now') is UTC and naive — tag it as UTC and
            # convert to local time so the displayed timestamp is correct.
            dt = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%b %d, %Y  %H:%M")
        except Exception:
            return dt_str

    def set_title(self, title: str):
        self.title_label.setText(title)


class _SearchHitWidget(QWidget):
    """A full-text search hit: which chat it came from plus a snippet."""

    def __init__(self, title: str, snippet: str, role: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        head = QLabel(f"{'You' if role == 'user' else 'Assistant'} · {title}")
        head.setStyleSheet(
            f"color:{theme.MUTED};font-size:{theme.FS_XS}px;font-weight:600;"
            f"background:transparent;"
        )
        body = QLabel(" ".join((snippet or "").split())[:120])
        body.setWordWrap(True)
        body.setStyleSheet(
            f"color:{theme.TEXT};font-size:{theme.FS_SM}px;background:transparent;"
        )
        lay.addWidget(head)
        lay.addWidget(body)


class ChatList(QWidget):
    """Left panel: chats grouped by pin/folder, plus full-text search results."""

    chat_selected = pyqtSignal(int)        # emits chat_id
    chats_deleted = pyqtSignal(list)       # emits the chat_ids that were removed
    new_chat_requested = pyqtSignal()
    message_selected = pyqtSignal(int, int)  # chat_id, message_id (search hit)

    def __init__(self, db, settings=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.settings = settings
        self._current_id: int | None = None
        self._show_archived = bool(
            settings.get("show_archived", False) if settings else False
        )
        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet(f"background:{theme.PANEL};")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        header.setStyleSheet(f"background:{theme.PANEL};")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 14, 14, 6)

        logo = QLabel("✦  PyQOA")
        logo.setStyleSheet(
            f"color:{theme.TEXT};font-size:{theme.FS_LG}px;font-weight:700;"
            f"background:transparent;"
        )
        hlay.addWidget(logo)
        hlay.addStretch()

        self.archive_btn = QPushButton("🗄")
        self.archive_btn.setCheckable(True)
        self.archive_btn.setChecked(self._show_archived)
        self.archive_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.archive_btn.setToolTip("Show archived chats")
        self.archive_btn.setFixedSize(26, 24)
        self.archive_btn.setStyleSheet(
            f"QPushButton{{background:transparent;color:{theme.FAINT};border:none;"
            f"font-size:{theme.FS_SM}px;border-radius:6px;}}"
            f"QPushButton:checked{{color:{theme.ACCENT};"
            f"background:{theme.SURFACE_SEL};}}"
        )
        self.archive_btn.toggled.connect(self._on_archive_toggled)
        hlay.addWidget(self.archive_btn)

        # Prominent full-width "New chat" button (modern sidebar pattern).
        new_btn = QPushButton("＋  New chat")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setToolTip("New chat  (Ctrl+N)")
        new_btn.setFixedHeight(38)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.SURFACE}; color:{theme.TEXT};
                border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS_SM}px;
                font-size:{theme.FS_MD}px; font-weight:600; text-align:center;
            }}
            QPushButton:hover {{ background:{theme.SURFACE_HI}; border-color:{theme.BORDER_HI}; }}
            QPushButton:pressed {{ background:{theme.SURFACE_SEL}; }}
        """)
        new_btn.clicked.connect(self.new_chat_requested)
        btn_wrap = QWidget()
        bwl = QVBoxLayout(btn_wrap)
        bwl.setContentsMargins(14, 4, 14, 6)
        bwl.addWidget(new_btn)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search chats and messages…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setStyleSheet(f"""
            QLineEdit {{
                background:{theme.SURFACE}; color:{theme.TEXT};
                border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS_SM}px;
                padding:7px 11px; font-size:{theme.FS_MD}px; margin:2px 14px 8px 14px;
            }}
            QLineEdit:focus {{ border:1px solid {theme.ACCENT}; }}
        """)
        # Full-text search runs on every keystroke, so debounce it.
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._filter)
        self.search_edit.textChanged.connect(
            lambda _: self._search_timer.start(SEARCH_DEBOUNCE_MS)
        )

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background:{theme.PANEL}; border:none; outline:none;
            }}
            QListWidget::item {{
                border-radius:{theme.RADIUS_SM}px; margin:1px 8px;
            }}
            QListWidget::item:selected {{
                background:{theme.SURFACE_SEL};
            }}
            QListWidget::item:hover:!selected {{
                background:{theme.SURFACE_HI};
            }}
        """)
        self.list_widget.setSpacing(2)
        self.list_widget.setFrameShape(self.list_widget.Shape.NoFrame)
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.list_widget.currentItemChanged.connect(self._on_current_changed)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)

        lay.addWidget(header)
        lay.addWidget(btn_wrap)
        lay.addWidget(self.search_edit)
        lay.addWidget(self.list_widget, stretch=1)

    def _add_header(self, text: str):
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)   # headers are labels, not rows
        item.setData(ROLE_KIND, KIND_HEADER)
        font = item.font()
        font.setBold(True)
        font.setPointSize(max(7, font.pointSize() - 1))
        item.setFont(font)
        item.setForeground(Qt.GlobalColor.gray)
        self.list_widget.addItem(item)

    def refresh(self, select_id: int | None = None):
        """Reload chats from the DB. Optionally highlight select_id."""
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        query = self.search_edit.text().strip()
        chats = (
            self.db.search_chats(query, include_archived=self._show_archived)
            if query
            else self.db.get_chats(include_archived=self._show_archived)
        )

        target_item = None
        pinned = [c for c in chats if c["pinned"]]
        rest = [c for c in chats if not c["pinned"]]
        groups: list[tuple[str, list]] = []
        if pinned:
            groups.append(("Pinned", pinned))
        by_folder: dict[str, list] = {}
        for chat in rest:
            by_folder.setdefault(chat["folder"] or NO_FOLDER, []).append(chat)
        for folder in sorted(by_folder, key=lambda f: (f == NO_FOLDER, f.lower())):
            groups.append((folder, by_folder[folder]))

        multiple_groups = len(groups) > 1
        for name, entries in groups:
            if multiple_groups:
                self._add_header(name)
            for chat in entries:
                item = QListWidgetItem()
                w = _ChatItemWidget(
                    chat["id"], chat["title"], chat["updated_at"],
                    pinned=bool(chat["pinned"]), archived=bool(chat["archived"]),
                )
                item.setSizeHint(w.sizeHint())
                item.setData(ROLE_CHAT_ID, chat["id"])
                item.setData(ROLE_KIND, KIND_CHAT)
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, w)
                if select_id is not None and chat["id"] == select_id:
                    target_item = item

        if query:
            self._append_message_hits(query)

        self.list_widget.blockSignals(False)

        if target_item:
            self.list_widget.setCurrentItem(target_item)
        elif select_id is None:
            first = self._first_chat_item()
            if first is not None:
                self.list_widget.setCurrentItem(first)

    def _append_message_hits(self, query: str):
        hits = self.db.search_messages(
            query, include_archived=self._show_archived
        )
        if not hits:
            return
        self._add_header(f"Messages ({len(hits)})")
        for hit in hits:
            item = QListWidgetItem()
            w = _SearchHitWidget(hit["title"], hit["snippet"], hit["role"])
            item.setSizeHint(w.sizeHint())
            item.setData(ROLE_CHAT_ID, hit["chat_id"])
            item.setData(ROLE_MESSAGE_ID, hit["message_id"])
            item.setData(ROLE_KIND, KIND_MESSAGE)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, w)

    def _first_chat_item(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(ROLE_KIND) == KIND_CHAT:
                return item
        return None

    def select_chat(self, chat_id: int):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if (
                item.data(ROLE_KIND) == KIND_CHAT
                and item.data(ROLE_CHAT_ID) == chat_id
            ):
                self.list_widget.setCurrentItem(item)
                return

    def _on_current_changed(self, current, _previous):
        if not current:
            return
        kind = current.data(ROLE_KIND)
        chat_id = current.data(ROLE_CHAT_ID)
        if kind == KIND_MESSAGE:
            self.message_selected.emit(chat_id, current.data(ROLE_MESSAGE_ID))
        elif kind == KIND_CHAT:
            self._current_id = chat_id
            self.chat_selected.emit(chat_id)

    def _on_archive_toggled(self, checked: bool):
        self._show_archived = checked
        if self.settings is not None:
            self.settings.set("show_archived", checked)
            self.settings.save()
        self.refresh(select_id=self._current_id)

    def _filter(self):
        self.refresh(select_id=self._current_id)

    def _selected_chat_ids(self) -> list[int]:
        ids = []
        for item in self.list_widget.selectedItems():
            if item.data(ROLE_KIND) == KIND_CHAT:
                cid = item.data(ROLE_CHAT_ID)
                if cid not in ids:
                    ids.append(cid)
        return ids

    def _context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item or item.data(ROLE_KIND) != KIND_CHAT:
            return
        chat_id = item.data(ROLE_CHAT_ID)
        selected = self._selected_chat_ids()
        if chat_id not in selected:
            selected = [chat_id]
        chat = self.db.get_chat(chat_id)
        multi = len(selected) > 1

        menu = QMenu(self)
        rename_act = menu.addAction("Rename")
        rename_act.setEnabled(not multi)
        pin_act = menu.addAction(
            "Unpin" if chat and chat["pinned"] else "Pin to top"
        )
        folder_act = menu.addAction("Move to folder…")
        archive_act = menu.addAction(
            "Unarchive" if chat and chat["archived"] else "Archive"
        )
        menu.addSeparator()
        export_menu = menu.addMenu("Export…")
        export_acts = {
            export_menu.addAction("Markdown"): "md",
            export_menu.addAction("JSON"): "json",
            export_menu.addAction("HTML"): "html",
            export_menu.addAction("PDF"): "pdf",
        }
        export_menu.setEnabled(not multi)
        menu.addSeparator()
        delete_act = menu.addAction(
            f"Delete {len(selected)} chats" if multi else "Delete"
        )

        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action is None:
            return
        if action == rename_act:
            self._rename(item, chat_id)
        elif action == pin_act:
            for cid in selected:
                self.db.set_pinned(cid, not (chat and chat["pinned"]))
            self.refresh(select_id=self._current_id)
        elif action == folder_act:
            self._move_to_folder(selected)
        elif action == archive_act:
            for cid in selected:
                self.db.set_archived(cid, not (chat and chat["archived"]))
            self.refresh(select_id=self._current_id)
        elif action in export_acts:
            self._export(chat_id, export_acts[action])
        elif action == delete_act:
            self._delete(selected)

    def _move_to_folder(self, chat_ids: list[int]):
        existing = self.db.folders()
        options = ["(no folder)"] + existing + ["New folder…"]
        choice, ok = QInputDialog.getItem(
            self, "Move to folder", "Folder:", options, 0, False
        )
        if not ok:
            return
        if choice == "New folder…":
            name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
            if not ok or not name.strip():
                return
            folder = name.strip()
        elif choice == "(no folder)":
            folder = None
        else:
            folder = choice
        for cid in chat_ids:
            self.db.set_folder(cid, folder)
        self.refresh(select_id=self._current_id)

    def _export(self, chat_id: int, fmt: str):
        chat = self.db.get_chat(chat_id)
        safe = chat_io.safe_filename(chat["title"] if chat else "chat")
        fn, suffix, filt = chat_io.EXPORTERS[fmt]
        caption = f"Export as {fmt.upper()}"
        path, _ = QFileDialog.getSaveFileName(self, caption, f"{safe}{suffix}", filt)
        if not path:
            return
        if not path.lower().endswith(suffix):
            path += suffix
        try:
            fn(self.db, chat_id, path)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def _rename(self, item, chat_id: int):
        w: _ChatItemWidget = self.list_widget.itemWidget(item)
        chat = self.db.get_chat(chat_id)
        current = chat["title"] if chat else (w.title_label.text() if w else "")
        text, ok = QInputDialog.getText(
            self, "Rename Chat", "New name:", text=current
        )
        if ok and text.strip():
            self.db.update_chat_title(chat_id, text.strip())
            self.refresh(select_id=chat_id)

    def _delete(self, chat_ids: list[int]):
        count = len(chat_ids)
        question = (
            f"Delete {count} chats and all their messages?" if count > 1
            else "Delete this chat and all its messages?"
        )
        reply = QMessageBox.question(
            self, "Delete Chat", question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_chats(chat_ids)
            self.chats_deleted.emit(chat_ids)
            self.refresh()
