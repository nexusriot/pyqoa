import os
import sys

from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QStatusBar,
    QFileDialog, QMessageBox, QApplication,
)
from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtCore import Qt, QTimer, QByteArray

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_io
import theme
from ui.chat_list import ChatList
from ui.chat_view import ChatView
from ui.settings_dialog import SettingsDialog


def _encode_qba(ba: QByteArray) -> str:
    """Serialise a Qt save-state blob to a base64 string for JSON storage."""
    return bytes(ba.toBase64()).decode("ascii")


def _decode_qba(s: str) -> QByteArray | None:
    if not s:
        return None
    try:
        return QByteArray.fromBase64(s.encode("ascii"))
    except Exception:
        return None


class MainWindow(QMainWindow):
    def __init__(self, settings, db, memory=None):
        super().__init__()
        self.settings = settings
        self.db = db
        self.memory = memory
        self._theme_pref = settings.get("theme", "dark")  # system/light/dark
        self.setWindowTitle("PyQOA")
        self.resize(1280, 820)
        self.setMinimumSize(800, 600)
        self._setup_ui()
        self._setup_menu()
        self._watch_system_theme()
        self._restore_geometry()
        QTimer.singleShot(0, self._startup_select)


    def _setup_ui(self):
        self._build_central()

        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self._status_bar)
        self._style_status_bar()
        self._status_bar.showMessage(f"Model: {self.settings.get('model', '')}")

        self.chat_view.status_updated.connect(self._status_bar.showMessage)

    def _build_central(self):
        """Create the splitter + chat list + chat view (also used on theme switch)."""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(
            f"QSplitter::handle{{background:{theme.BORDER};}}"
            f"QSplitter{{background:{theme.BG};}}"
        )

        self.chat_list = ChatList(self.db)
        self.chat_list.setMinimumWidth(220)
        self.chat_list.setMaximumWidth(360)

        self.chat_view = ChatView(self.settings, self.db, self.memory)

        self.chat_list.chat_selected.connect(self._on_chat_selected)
        self.chat_list.chat_deleted.connect(self._on_chat_deleted)
        self.chat_list.new_chat_requested.connect(self._new_chat)
        self.chat_view.new_chat_requested.connect(self._new_chat)
        self.chat_view.chat_updated.connect(self._on_chat_updated)

        splitter.addWidget(self.chat_list)
        splitter.addWidget(self.chat_view)

        state = _decode_qba(self.settings.get("splitter_state", ""))
        if state is None or not splitter.restoreState(state):
            splitter.setSizes([260, 1020])

        self.splitter = splitter
        # setCentralWidget takes ownership and deletes any previous central widget.
        self.setCentralWidget(splitter)

    def _restore_geometry(self):
        geo = _decode_qba(self.settings.get("window_geometry", ""))
        if geo is not None:
            self.restoreGeometry(geo)

    def _save_window_state(self):
        self.settings.set("window_geometry", _encode_qba(self.saveGeometry()))
        if hasattr(self, "splitter"):
            self.settings.set("splitter_state", _encode_qba(self.splitter.saveState()))
        self.settings.save()

    def _style_status_bar(self):
        self._status_bar.setStyleSheet(
            f"QStatusBar{{background:{theme.PANEL};color:{theme.FAINT};font-size:12px;"
            f"border-top:1px solid {theme.BORDER};}}"
            f"QStatusBar::item{{border:none;}}"
        )

    def apply_theme(self, pref: str):
        """Set the theme *preference* (system/light/dark), persist it, and restyle.

        "system" follows the OS colour scheme; the concrete palette is resolved here.
        """
        if pref not in ("system", "light", "dark"):
            pref = "dark"
        self._theme_pref = pref
        self.settings.set("theme", pref)
        self.settings.save()

        # setChecked emits `toggled` (not `triggered`), so the exclusive QActionGroup
        # unchecks the other radios without re-invoking apply_theme.
        act = getattr(self, "_theme_actions", {}).get(pref)
        if act is not None:
            act.setChecked(True)

        theme.apply(theme.resolve(pref))
        self._relayout_for_theme()
        label = pref.capitalize()
        if pref == "system":
            label += f" ({theme.NAME})"
        self._status_bar.showMessage(f"Theme: {label}")

    def _relayout_for_theme(self):
        """Re-apply palette + global QSS and rebuild the UI so widgets restyle."""
        # Carry the current splitter position over to the rebuilt splitter.
        if hasattr(self, "splitter"):
            self.settings.set("splitter_state", _encode_qba(self.splitter.saveState()))

        app = QApplication.instance()
        if app is not None:
            app.setPalette(theme.qpalette())
            app.setStyleSheet(theme.global_qss())

        # Stop any in-flight stream before the old chat view is torn down.
        if self.chat_view.stream_worker and self.chat_view.stream_worker.isRunning():
            self.chat_view.stream_worker.blockSignals(True)
            self.chat_view.stream_worker.cancel()
            self.chat_view.stream_worker.wait(2000)

        current = self.chat_view.current_chat_id
        self._build_central()
        self.chat_view.status_updated.connect(self._status_bar.showMessage)
        self._style_status_bar()

        # refresh(select_id=…) re-selects the row, which loads it via chat_selected.
        chats = self.db.get_chats()
        if current is not None and any(c["id"] == current for c in chats):
            self.chat_list.refresh(select_id=current)
        elif chats:
            self.chat_list.refresh(select_id=chats[0]["id"])

    def _watch_system_theme(self):
        """Follow OS light/dark changes while the 'system' preference is active."""
        app = QApplication.instance()
        if app is None:
            return
        hints = app.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_scheme_changed)

    def _on_system_scheme_changed(self, *_):
        if self._theme_pref != "system":
            return
        resolved = theme.resolve("system")
        if resolved != theme.NAME:
            theme.apply(resolved)
            self._relayout_for_theme()
            self._status_bar.showMessage(f"Theme: System ({resolved})")

    def _toggle_theme(self):
        """Quick flip between light and dark (overrides a 'system' preference)."""
        self.apply_theme("light" if theme.NAME == "dark" else "dark")

    def _setup_menu(self):
        # Menu bar / menus are themed globally via theme.global_qss().
        bar = self.menuBar()

        file_menu = bar.addMenu("File")
        a_new = file_menu.addAction("New Chat")
        a_new.setShortcut("Ctrl+N")
        a_new.triggered.connect(self._new_chat)

        a_import = file_menu.addAction("Import Chat (JSON)…")
        a_import.triggered.connect(self._import_chat)

        file_menu.addSeparator()

        a_settings = file_menu.addAction("Settings…")
        a_settings.setShortcut("Ctrl+,")
        a_settings.triggered.connect(self._open_settings)

        file_menu.addSeparator()

        a_quit = file_menu.addAction("Quit")
        a_quit.setShortcut("Ctrl+Q")
        a_quit.triggered.connect(self.close)

        view_menu = bar.addMenu("View")
        theme_menu = view_menu.addMenu("Theme")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}
        for pref, label in (("system", "System"), ("light", "Light"), ("dark", "Dark")):
            act = QAction(label, self, checkable=True)
            act.setChecked(self._theme_pref == pref)
            act.triggered.connect(lambda checked, p=pref: checked and self.apply_theme(p))
            self._theme_group.addAction(act)
            theme_menu.addAction(act)
            self._theme_actions[pref] = act

        a_toggle = view_menu.addAction("Toggle Light / Dark")
        a_toggle.setShortcut("Ctrl+Shift+L")
        a_toggle.triggered.connect(self._toggle_theme)

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

    def _import_chat(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Chat", "", "JSON (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            chat_id = chat_io.import_json(self.db, path)
        except Exception as exc:
            QMessageBox.critical(self, "Import failed", str(exc))
            return
        self.chat_list.refresh(select_id=chat_id)
        self.chat_view.load_chat(chat_id)
        self._status_bar.showMessage("Chat imported.")

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
        self._save_window_state()
        if self.chat_view.stream_worker and self.chat_view.stream_worker.isRunning():
            self.chat_view.stream_worker.cancel()
            self.chat_view.stream_worker.wait(3000)
        event.accept()
