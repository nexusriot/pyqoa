import json
import urllib.request
import urllib.error

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QLabel, QGroupBox, QPushButton,
    QCheckBox, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal


_PROVIDERS = {
    "OpenAI":  {"url": "https://api.openai.com/v1",  "key_required": True},
    "Ollama":  {"url": "http://localhost:11434/v1",   "key_required": False},
    "Custom":  {"url": "",                            "key_required": True},
}


class _ModelFetcher(QThread):
    """Fetches the available model list from either Ollama or an OpenAI-compat API."""

    done  = pyqtSignal(list)   # list[str]
    error = pyqtSignal(str)

    def __init__(self, base_url: str, api_key: str, parent=None):
        super().__init__(parent)
        self._url = base_url.rstrip("/")
        self._key = api_key

    def run(self):
        try:
            models = self._fetch()
            self.done.emit(models)
        except Exception as exc:
            self.error.emit(str(exc))

    def _fetch(self) -> list:
        url = self._url

        is_local = any(h in url for h in ("localhost", "127.0.0.1", "::1", ":11434"))
        if is_local:
            ollama_base = url.replace("/v1", "").rstrip("/")
            try:
                with urllib.request.urlopen(
                    f"{ollama_base}/api/tags", timeout=5
                ) as resp:
                    data = json.loads(resp.read())
                return sorted(m["name"] for m in data.get("models", []))
            except Exception:
                pass  # fall through to OpenAI-compat

        req = urllib.request.Request(
            f"{url}/models",
            headers={"Authorization": f"Bearer {self._key or 'none'}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return sorted(m["id"] for m in data.get("data", []))


_STYLE = """
QDialog      { background:#0f172a; color:#ececf1; }
QGroupBox    { color:#64748b; border:1px solid #1e293b; border-radius:8px;
               margin-top:12px; padding-top:12px; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 6px;
                   color:#94a3b8; font-size:12px; }
QLabel       { color:#cbd5e1; }
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background:#1e293b; color:#ececf1;
    border:1px solid #334155; border-radius:6px; padding:6px 10px;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus { border-color:#3b82f6; }
QComboBox::drop-down { border:none; width:22px; }
QComboBox QAbstractItemView {
    background:#1e293b; color:#ececf1;
    selection-background-color:#2563eb;
    border:1px solid #334155;
    outline:none;
}
QCheckBox { color:#cbd5e1; }
QPushButton {
    background:#1e293b; color:#ececf1;
    border:1px solid #334155; border-radius:6px; padding:6px 14px;
}
QPushButton:hover  { background:#334155; }
QPushButton:pressed{ background:#475569; }
QPushButton:checked{ background:#1e3a5f; color:#60a5fa; border:1px solid #3b82f6; }
QPushButton:disabled{ background:#0f172a; color:#334155; border-color:#1e293b; }
QScrollBar:vertical { background:#0f172a; width:8px; border:none; }
QScrollBar::handle:vertical { background:#334155; border-radius:4px; min-height:20px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._fetcher: _ModelFetcher | None = None
        self.setWindowTitle("Settings")
        self.setMinimumWidth(580)
        self.setStyleSheet(_STYLE)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 16)

        # Inline validation error banner (hidden by default)
        self._error_label = QLabel()
        self._error_label.setStyleSheet(
            "color:#fca5a5;background:#450a0a;border:1px solid #7f1d1d;"
            "border-radius:6px;padding:8px 12px;font-size:13px;"
        )
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        root.addWidget(self._error_label)

        root.addWidget(self._api_group())
        root.addWidget(self._param_group())
        root.addWidget(self._memory_group())
        root.addWidget(self._system_group())
        root.addWidget(self._button_box())

    def _api_group(self) -> QGroupBox:
        box = QGroupBox("API Configuration")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(6)
        self._preset_btns: dict[str, QPushButton] = {}

        for name in _PROVIDERS:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.clicked.connect(lambda _, n=name: self._apply_preset(n))
            preset_row.addWidget(btn)
            self._preset_btns[name] = btn

        preset_row.addStretch()
        form.addRow("Provider:", preset_row)

        self.url_edit = QLineEdit(self.settings.get("api_url", ""))
        self.url_edit.setPlaceholderText("https://api.openai.com/v1")
        self.url_edit.textChanged.connect(self._sync_preset_buttons)
        self.url_edit.textChanged.connect(lambda _: self._error_label.hide())
        form.addRow("Base URL:", self.url_edit)

        self.key_edit = QLineEdit(self.settings.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-…  (not required for Ollama)")
        self.key_edit.textChanged.connect(lambda _: self._error_label.hide())

        show_btn = QPushButton("Show")
        show_btn.setFixedWidth(56)
        show_btn.setCheckable(True)
        show_btn.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_edit)
        key_row.addWidget(show_btn)
        form.addRow("API Key:", key_row)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model_combo.addItem(self.settings.get("model", "gpt-4o"))
        self.model_combo.setCurrentText(self.settings.get("model", "gpt-4o"))
        self.model_combo.currentTextChanged.connect(lambda _: self._error_label.hide())

        self.fetch_btn = QPushButton("Fetch Models")
        self.fetch_btn.setFixedWidth(110)
        self.fetch_btn.setToolTip("Load available models from the configured endpoint")
        self.fetch_btn.clicked.connect(self._fetch_models)

        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo)
        model_row.addWidget(self.fetch_btn)
        form.addRow("Model:", model_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#64748b;font-size:12px;")
        form.addRow("", self._status_label)

        self._sync_preset_buttons()

        return box

    def _param_group(self) -> QGroupBox:
        box = QGroupBox("Parameters")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 600)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(int(self.settings.get("timeout", 60)))

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 131072)
        self.max_tokens_spin.setSingleStep(256)
        self.max_tokens_spin.setValue(int(self.settings.get("max_tokens", 4096)))

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(float(self.settings.get("temperature", 0.7)))

        self.stream_check = QCheckBox("Streaming responses")
        self.stream_check.setChecked(bool(self.settings.get("stream", True)))

        form.addRow("Timeout:", self.timeout_spin)
        form.addRow("Max tokens:", self.max_tokens_spin)
        form.addRow("Temperature:", self.temp_spin)
        form.addRow("", self.stream_check)

        return box

    def _memory_group(self) -> QGroupBox:
        box = QGroupBox("Chat Memory")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.memory_enabled_check = QCheckBox("Enable sliding-window memory")
        self.memory_enabled_check.setChecked(
            bool(self.settings.get("memory_enabled", True))
        )

        self.window_size_spin = QSpinBox()
        self.window_size_spin.setRange(2, 200)
        self.window_size_spin.setSuffix(" messages")
        self.window_size_spin.setValue(int(self.settings.get("memory_window_size", 20)))

        self.vector_check = QCheckBox(
            "Use vector retrieval for older messages (requires chromadb)"
        )
        self.vector_check.setChecked(
            bool(self.settings.get("memory_use_vector", False))
        )

        self.top_k_spin = QSpinBox()
        self.top_k_spin.setRange(1, 20)
        self.top_k_spin.setValue(int(self.settings.get("memory_top_k", 4)))

        self.embed_model_edit = QLineEdit(
            self.settings.get("memory_embed_model", "text-embedding-3-small")
        )
        self.embed_model_edit.setPlaceholderText(
            "text-embedding-3-small  (OpenAI)  /  nomic-embed-text  (Ollama)"
        )

        self.embed_url_edit = QLineEdit(self.settings.get("memory_embed_url", ""))
        self.embed_url_edit.setPlaceholderText(
            "Leave blank to reuse the chat Base URL"
        )

        form.addRow("", self.memory_enabled_check)
        form.addRow("Window size:", self.window_size_spin)
        form.addRow("", self.vector_check)
        form.addRow("Retrieve top-k:", self.top_k_spin)
        form.addRow("Embedding model:", self.embed_model_edit)
        form.addRow("Embedding URL:", self.embed_url_edit)

        try:
            from memory import chroma_available
            chroma_ok = chroma_available()
        except Exception:
            chroma_ok = False
        if not chroma_ok:
            note = QLabel(
                "chromadb is not installed — vector retrieval is unavailable. "
                "Install it with: pip install chromadb"
            )
            note.setStyleSheet("color:#f59e0b;font-size:11px;")
            note.setWordWrap(True)
            form.addRow("", note)
            self.vector_check.setEnabled(False)
            self.vector_check.setChecked(False)

        return box

    def _system_group(self) -> QGroupBox:
        box = QGroupBox("System Prompt")
        lay = QVBoxLayout(box)
        self.system_edit = QTextEdit()
        self.system_edit.setPlainText(self.settings.get("system_prompt", ""))
        self.system_edit.setFixedHeight(80)
        lay.addWidget(self.system_edit)
        return box

    def _button_box(self) -> QDialogButtonBox:
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(
            "background:#2563eb;color:white;font-weight:bold;"
            "border:none;border-radius:6px;padding:6px 18px;"
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        return btns


    def _apply_preset(self, name: str):
        info = _PROVIDERS[name]
        if info["url"]:
            self.url_edit.setText(info["url"])
        if not info["key_required"]:
            self.key_edit.clear()
            self.key_edit.setPlaceholderText("Not required for Ollama")
        else:
            self.key_edit.setPlaceholderText("sk-…")
        self._sync_preset_buttons()
        # Auto-fetch models when switching to Ollama
        if name == "Ollama":
            QTimer.singleShot(100, self._fetch_models)

    def _sync_preset_buttons(self):
        current_url = self.url_edit.text().strip()
        matched = "Custom"
        for name, info in _PROVIDERS.items():
            if info["url"] and current_url == info["url"]:
                matched = name
                break
        for name, btn in self._preset_btns.items():
            btn.blockSignals(True)
            btn.setChecked(name == matched)
            btn.blockSignals(False)

    def _fetch_models(self):
        if self._fetcher and self._fetcher.isRunning():
            return
        url = self.url_edit.text().strip()
        if not url:
            self._set_status("Enter a Base URL first.", error=True)
            return
        self.fetch_btn.setEnabled(False)
        self._set_status("Fetching models…")

        self._fetcher = _ModelFetcher(url, self.key_edit.text().strip(), parent=self)
        self._fetcher.done.connect(self._on_models_fetched)
        self._fetcher.error.connect(self._on_fetch_error)
        self._fetcher.start()

    def _on_models_fetched(self, models: list):
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        idx = self.model_combo.findText(current)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.setCurrentText(current)
        self._set_status(f"{len(models)} model(s) loaded.")
        self.fetch_btn.setEnabled(True)

    def _on_fetch_error(self, error: str):
        self._set_status(f"Failed to fetch models: {error}", error=True)
        self.fetch_btn.setEnabled(True)

    def _set_status(self, text: str, error: bool = False):
        color = "#ef4444" if error else "#64748b"
        self._status_label.setStyleSheet(f"color:{color};font-size:12px;")
        self._status_label.setText(text)


    def _save(self):
        url = self.url_edit.text().strip()
        model = self.model_combo.currentText().strip()
        key = self.key_edit.text().strip()

        errors = []
        if not url:
            errors.append("Base URL is required.")
        elif not (url.startswith("http://") or url.startswith("https://")):
            errors.append("Base URL must start with http:// or https://")
        if not model:
            errors.append("Model name is required.")

        is_local = any(h in url for h in ("localhost", "127.0.0.1", "::1", ":11434"))
        if url and not is_local and not key:
            errors.append("API key is required for non-local endpoints.")

        if errors:
            self._error_label.setText("  ·  ".join(errors))
            self._error_label.show()
            return

        self._error_label.hide()
        self.settings.update(
            {
                "api_url":       url,
                "api_key":       key,
                "model":         model,
                "timeout":       self.timeout_spin.value(),
                "max_tokens":    self.max_tokens_spin.value(),
                "temperature":   self.temp_spin.value(),
                "stream":        self.stream_check.isChecked(),
                "system_prompt": self.system_edit.toPlainText().strip(),
                "memory_enabled":     self.memory_enabled_check.isChecked(),
                "memory_window_size": self.window_size_spin.value(),
                "memory_use_vector":  self.vector_check.isChecked(),
                "memory_top_k":       self.top_k_spin.value(),
                "memory_embed_model": self.embed_model_edit.text().strip(),
                "memory_embed_url":   self.embed_url_edit.text().strip(),
            }
        )
        self.settings.save()
        self.accept()
