from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QWidget, QLabel, QStatusBar,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut

from ui.chat_list import ChatList
from ui.chat_view import ChatView
from ui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self, settings, db, memory=None):
        super().__init__()
        self.settings = settings
        self.db = db
        self.memory = memory
        self.setWindowTitle("PyQOA")
        self.resize(1280, 820)
        self.setMinimumSize(800, 600)
        self._setup_ui()
        self._setup_menu()
        self._setup_shortcuts()
        QTimer.singleShot(0, self._startup_select)


    def _setup_ui(self):
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            "QSplitter::handle{background:#1e293b;}"
            "QSplitter{background:#0f172a;}"
        )

        self.chat_list = ChatList(self.db)
        self.chat_list.setMinimumWidth(200)
        self.chat_list.setMaximumWidth(340)

        self.chat_view = ChatView(self.settings, self.db, self.memory)

        self.chat_list.chat_selected.connect(self._on_chat_selected)
        self.chat_list.chat_deleted.connect(self._on_chat_deleted)
        self.chat_list.new_chat_requested.connect(self._new_chat)
        self.chat_view.chat_updated.connect(self._on_chat_updated)

        splitter.addWidget(self.chat_list)
        splitter.addWidget(self.chat_view)
        splitter.setSizes([260, 1020])

        self.setCentralWidget(splitter)

        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "QStatusBar{background:#0a0f1e;color:#4b5563;font-size:12px;"
            "border-top:1px solid #1e293b;}"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage(f"Model: {self.settings.get('model', '')}")

        self.chat_view.status_updated.connect(self._status_bar.showMessage)

    def _setup_menu(self):
        bar = self.menuBar()
        bar.setStyleSheet(
            "QMenuBar{background:#0a0f1e;color:#ececf1;border-bottom:1px solid #1e293b;}"
            "QMenuBar::item:selected{background:#1e3a5f;}"
            "QMenu{background:#0f172a;color:#ececf1;border:1px solid #1e293b;}"
            "QMenu::item:selected{background:#2563eb;}"
        )

        file_menu = bar.addMenu("File")
        a_new = file_menu.addAction("New Chat")
        a_new.setShortcut("Ctrl+N")
        a_new.triggered.connect(self._new_chat)

        file_menu.addSeparator()

        a_settings = file_menu.addAction("Settings…")
        a_settings.setShortcut("Ctrl+,")
        a_settings.triggered.connect(self._open_settings)

        file_menu.addSeparator()

        a_quit = file_menu.addAction("Quit")
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.close)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self._new_chat)
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(self._open_settings)


    def _startup_select(self):
        chats = self.db.get_chats()
        if chats:
            self.chat_list.refresh(select_id=chats[0]["id"])
        else:
            self._new_chat()

    def _new_chat(self):
        chat_id = self.db.create_chat()
        self.chat_list.refresh(select_id=chat_id)
        self.chat_view.load_chat(chat_id)

    def _on_chat_selected(self, chat_id: int):
        self.chat_view.load_chat(chat_id)

    def _on_chat_deleted(self, chat_id: int):
        if self.memory:
            self.memory.reset_chat(chat_id)
        chats = self.db.get_chats()
        if chats:
            cid = chats[0]["id"]
            self.chat_list.refresh(select_id=cid)
            self.chat_view.load_chat(cid)
        else:
            self._new_chat()

    def _on_chat_updated(self, chat_id: int):
        self.chat_list.refresh(select_id=chat_id)

    def _open_settings(self):
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            # Refresh model badge if settings changed
            if self.chat_view.current_chat_id:
                self.chat_view._model_badge.setText(self.settings.get("model", ""))
            self._status_bar.showMessage(f"Model: {self.settings.get('model', '')}")

    def closeEvent(self, event):
        if self.chat_view.stream_worker and self.chat_view.stream_worker.isRunning():
            self.chat_view.stream_worker.cancel()
            self.chat_view.stream_worker.wait(3000)
        event.accept()
