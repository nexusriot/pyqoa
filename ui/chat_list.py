import os
import sys
from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QMessageBox,
    QLineEdit, QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_io
import theme


class _ChatItemWidget(QWidget):
    """Custom widget rendered inside each QListWidgetItem."""

    def __init__(self, chat_id: int, title: str, updated_at: str, parent=None):
        super().__init__(parent)
        self.chat_id = chat_id
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(2)

        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(
            f"color:{theme.TEXT};font-size:13px;font-weight:500;background:transparent;"
        )
        self.title_label.setWordWrap(False)

        date_label = QLabel(self._fmt(updated_at))
        date_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:11px;background:transparent;"
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


class ChatList(QWidget):
    """Left panel: list of saved chats with New/Rename/Delete actions."""

    chat_selected = pyqtSignal(int)   # emits chat_id
    chat_deleted = pyqtSignal(int)    # emits chat_id that was removed
    new_chat_requested = pyqtSignal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._current_id: int | None = None
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
            f"color:{theme.TEXT};font-size:16px;font-weight:700;background:transparent;"
        )
        hlay.addWidget(logo)
        hlay.addStretch()

        # Prominent full-width "New chat" button (modern sidebar pattern).
        new_btn = QPushButton("＋  New chat")
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setToolTip("New chat  (Ctrl+N)")
        new_btn.setFixedHeight(38)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background:{theme.SURFACE}; color:{theme.TEXT};
                border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS_SM}px;
                font-size:13px; font-weight:600; text-align:center;
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
        self.search_edit.setPlaceholderText("Search chats…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setStyleSheet(f"""
            QLineEdit {{
                background:{theme.SURFACE}; color:{theme.TEXT};
                border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS_SM}px;
                padding:7px 11px; font-size:13px; margin:2px 14px 8px 14px;
            }}
            QLineEdit:focus {{ border:1px solid {theme.ACCENT}; }}
        """)
        self.search_edit.textChanged.connect(self._filter)

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
        self.list_widget.currentItemChanged.connect(self._on_current_changed)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)

        lay.addWidget(header)
        lay.addWidget(btn_wrap)
        lay.addWidget(self.search_edit)
        lay.addWidget(self.list_widget, stretch=1)


    def refresh(self, select_id: int | None = None):
        """Reload chats from DB. Optionally highlight select_id."""
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        query = self.search_edit.text().strip().lower()
        chats = self.db.get_chats()

        target_item = None
        for chat in chats:
            if query and query not in chat["title"].lower():
                continue
            item = QListWidgetItem()
            w = _ChatItemWidget(chat["id"], chat["title"], chat["updated_at"])
            item.setSizeHint(w.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, chat["id"])
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, w)
            if select_id is not None and chat["id"] == select_id:
                target_item = item

        self.list_widget.blockSignals(False)

        if target_item:
            self.list_widget.setCurrentItem(target_item)
        elif self.list_widget.count() > 0 and select_id is None:
            self.list_widget.setCurrentRow(0)

    def select_chat(self, chat_id: int):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == chat_id:
                self.list_widget.setCurrentItem(item)
                return


    def _on_current_changed(self, current, _previous):
        if current:
            cid = current.data(Qt.ItemDataRole.UserRole)
            self._current_id = cid
            self.chat_selected.emit(cid)

    def _filter(self, _text: str):
        self.refresh(select_id=self._current_id)

    def _context_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        chat_id = item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)
        rename_act = menu.addAction("Rename")
        export_md_act = menu.addAction("Export as Markdown…")
        export_json_act = menu.addAction("Export as JSON…")
        menu.addSeparator()
        delete_act = menu.addAction("Delete")

        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action == rename_act:
            self._rename(item, chat_id)
        elif action == export_md_act:
            self._export(item, chat_id, "md")
        elif action == export_json_act:
            self._export(item, chat_id, "json")
        elif action == delete_act:
            self._delete(chat_id)

    def _export(self, item, chat_id: int, fmt: str):
        w: _ChatItemWidget = self.list_widget.itemWidget(item)
        title = w.title_label.text() if w else "chat"
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()
        safe = safe or "chat"
        if fmt == "md":
            caption, filt, suffix, fn = (
                "Export as Markdown", "Markdown (*.md)", ".md", chat_io.export_markdown
            )
        else:
            caption, filt, suffix, fn = (
                "Export as JSON", "JSON (*.json)", ".json", chat_io.export_json
            )
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
        current = w.title_label.text()
        text, ok = QInputDialog.getText(
            self, "Rename Chat", "New name:", text=current
        )
        if ok and text.strip():
            self.db.update_chat_title(chat_id, text.strip())
            self.refresh(select_id=chat_id)

    def _delete(self, chat_id: int):
        reply = QMessageBox.question(
            self, "Delete Chat",
            "Delete this chat and all its messages?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_chat(chat_id)
            self.chat_deleted.emit(chat_id)
            self.refresh()
