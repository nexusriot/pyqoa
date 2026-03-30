import json
import urllib.request
import urllib.error

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QLabel, QGroupBox, QPushButton,
    QCheckBox, QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal


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
QDialog      { background:#1a1b26; color:#ececf1; }
QGroupBox    { color:#9ca3af; border:1px solid #374151; border-radius:6px;
               margin-top:10px; padding-top:10px; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 4px; }
QLabel       { color:#d1d5db; }
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background:#2d3748; color:#ececf1;
    border:1px solid #4b5563; border-radius:6px; padding:6px 10px;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus { border-color:#3b82f6; }
QComboBox::drop-down { border:none; width:22px; }
QComboBox QAbstractItemView {
    background:#2d3748; color:#ececf1;
    selection-background-color:#2563eb;
    border:1px solid #4b5563;
}
QCheckBox { color:#d1d5db; }
QPushButton {
    background:#374151; color:#ececf1;
    border:none; border-radius:6px; padding:6px 14px;
}
QPushButton:hover  { background:#4b5563; }
QPushButton:pressed{ background:#6b7280; }
QPushButton:checked{ background:#1e3a5f; color:#60a5fa; border:1px solid #3b82f6; }
QPushButton:disabled{ background:#1f2937; color:#4b5563; }
"""


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._fetcher: _ModelFetcher | None = None
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        self.setStyleSheet(_STYLE)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(14)
        root.setContentsMargins(20, 16, 20, 16)

        root.addWidget(self._api_group())
        root.addWidget(self._param_group())
        root.addWidget(self._system_group())
        root.addWidget(self._button_box())

    def _api_group(self) -> QGroupBox:
        box = QGroupBox("API")
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
        form.addRow("Base URL:", self.url_edit)


        self.key_edit = QLineEdit(self.settings.get("api_key", ""))
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-…  (not required for Ollama)")

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

        self.fetch_btn = QPushButton("Fetch Models")
        self.fetch_btn.setFixedWidth(110)
        self.fetch_btn.setToolTip("Load available models from the configured endpoint")
        self.fetch_btn.clicked.connect(self._fetch_models)

        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo)
        model_row.addWidget(self.fetch_btn)
        form.addRow("Model:", model_row)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color:#6b7280;font-size:12px;")
        form.addRow("", self._status_label)

        # Highlight correct preset button on open
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
            "border-radius:6px;padding:6px 18px;"
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
        self.fetch_btn.setEnabled(False)
        self._status_label.setText("Fetching models…")

        self._fetcher = _ModelFetcher(
            self.url_edit.text().strip(),
            self.key_edit.text().strip(),
            parent=self,
        )
        self._fetcher.done.connect(self._on_models_fetched)
        self._fetcher.error.connect(self._on_fetch_error)
        self._fetcher.start()

    def _on_models_fetched(self, models: list):
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        # Restore previously selected model if still present, else keep typed value
        idx = self.model_combo.findText(current)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        else:
            self.model_combo.setCurrentText(current)
        self._status_label.setText(f"{len(models)} model(s) loaded.")
        self.fetch_btn.setEnabled(True)

    def _on_fetch_error(self, error: str):
        self._status_label.setText(f"Error: {error}")
        self.fetch_btn.setEnabled(True)



    def _save(self):
        self.settings.update(
            {
                "api_url":       self.url_edit.text().strip(),
                "api_key":       self.key_edit.text().strip(),
                "model":         self.model_combo.currentText().strip(),
                "timeout":       self.timeout_spin.value(),
                "max_tokens":    self.max_tokens_spin.value(),
                "temperature":   self.temp_spin.value(),
                "stream":        self.stream_check.isChecked(),
                "system_prompt": self.system_edit.toPlainText().strip(),
            }
        )
        self.settings.save()
        self.accept()
