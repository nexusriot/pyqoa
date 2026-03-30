from datetime import datetime, timezone

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QListWidget, QListWidgetItem, QMenu, QInputDialog, QMessageBox,
    QLineEdit,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon


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
            "color:#ececf1;font-size:13px;background:transparent;"
        )
        self.title_label.setWordWrap(False)

        date_label = QLabel(self._fmt(updated_at))
        date_label.setStyleSheet(
            "color:#6b7280;font-size:11px;background:transparent;"
        )

        lay.addWidget(self.title_label)
        lay.addWidget(date_label)

    @staticmethod
    def _fmt(dt_str: str) -> str:
        try:
            dt = datetime.fromisoformat(dt_str)
            return dt.strftime("%b %d, %Y  %H:%M")
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
        self.setStyleSheet("background:#111827;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        header.setStyleSheet("background:#0f172a;")
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(14, 12, 10, 12)

        logo = QLabel("PyQOA")
        logo.setStyleSheet("color:#ececf1;font-size:16px;font-weight:bold;background:transparent;")

        new_btn = QPushButton("+")
        new_btn.setFixedSize(30, 30)
        new_btn.setToolTip("New chat  (Ctrl+N)")
        new_btn.setStyleSheet("""
            QPushButton {
                background:#1e3a5f; color:#60a5fa;
                border-radius:15px; font-size:20px; font-weight:bold;
            }
            QPushButton:hover { background:#2563eb; color:white; }
        """)
        new_btn.clicked.connect(self.new_chat_requested)

        hlay.addWidget(logo)
        hlay.addStretch()
        hlay.addWidget(new_btn)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search chats…")
        self.search_edit.setStyleSheet("""
            QLineEdit {
                background:#1f2937; color:#ececf1;
                border:none; border-radius:6px;
                padding:6px 10px; font-size:13px; margin:8px 10px;
            }
        """)
        self.search_edit.textChanged.connect(self._filter)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet("""
            QListWidget {
                background:#111827; border:none; outline:none;
            }
            QListWidget::item {
                border-radius:6px; margin:1px 6px;
            }
            QListWidget::item:selected {
                background:#1e3a5f;
            }
            QListWidget::item:hover:!selected {
                background:#1f2937;
            }
        """)
        self.list_widget.setSpacing(1)
        self.list_widget.currentItemChanged.connect(self._on_current_changed)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._context_menu)

        lay.addWidget(header)
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
        menu.addSeparator()
        delete_act = menu.addAction("Delete")

        action = menu.exec(self.list_widget.mapToGlobal(pos))
        if action == rename_act:
            self._rename(item, chat_id)
        elif action == delete_act:
            self._delete(chat_id)

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
