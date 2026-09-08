import json
import os
import sys
import urllib.request
import urllib.error

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox,
    QDialogButtonBox, QLabel, QGroupBox, QPushButton,
    QCheckBox, QComboBox, QTabWidget, QWidget, QListWidget, QListWidgetItem,
    QInputDialog, QMessageBox, QScrollArea,
)
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import keystore
import theme
import tools as tool_module
from api_client import is_local_url
from memory import chroma_available


def headers_to_text(headers: dict) -> str:
    """Render a header dict as editable "Name: value" lines."""
    if not isinstance(headers, dict):
        return ""
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


def text_to_headers(text: str) -> dict:
    """Parse "Name: value" lines back into a header dict (blank lines ignored)."""
    out: dict = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        if name:
            out[name] = value.strip()
    return out


_PROVIDERS = {
    "OpenAI":  {"url": "https://api.openai.com/v1",  "key_required": True},
    "Ollama":  {"url": "http://localhost:11434/v1",   "key_required": False},
    "Custom":  {"url": "",                            "key_required": True},
}


class _ModelFetcher(QThread):
    """Fetches the available model list from either Ollama or an OpenAI-compat API."""

    done  = pyqtSignal(list)   # list[str]
    error = pyqtSignal(str)

    def __init__(self, base_url: str, api_key: str, headers: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self._url = base_url.rstrip("/")
        self._key = api_key
        self._headers = dict(headers or {})

    def run(self):
        try:
            models = self._fetch()
            self.done.emit(models)
        except Exception as exc:
            self.error.emit(str(exc))

    def _fetch(self) -> list:
        url = self._url

        if is_local_url(url):
            ollama_base = url.replace("/v1", "").rstrip("/")
            try:
                with urllib.request.urlopen(
                    f"{ollama_base}/api/tags", timeout=5
                ) as resp:
                    data = json.loads(resp.read())
                return sorted(m["name"] for m in data.get("models", []))
            except Exception:
                pass  # fall through to OpenAI-compat

        headers = {"Authorization": f"Bearer {self._key or 'none'}"}
        headers.update(self._headers)
        req = urllib.request.Request(f"{url}/models", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return sorted(m["id"] for m in data.get("data", []))


def _style() -> str:
    """Dialog stylesheet, built per-open so it tracks the active theme."""
    return f"""
QDialog      {{ background:{theme.PANEL}; color:{theme.TEXT}; }}
QGroupBox    {{ color:{theme.FAINT}; border:1px solid {theme.BORDER}; border-radius:{theme.RADIUS_SM}px;
               margin-top:12px; padding-top:12px; }}
QGroupBox::title {{ subcontrol-origin:margin; left:12px; padding:0 6px;
                   color:{theme.MUTED}; font-size:{theme.FS_SM}px; }}
QLabel       {{ color:{theme.MUTED}; }}
QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background:{theme.SURFACE}; color:{theme.TEXT};
    border:1px solid {theme.BORDER}; border-radius:6px; padding:6px 10px;
}}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{ border:1px solid {theme.ACCENT}; }}
QComboBox::drop-down {{ border:none; width:22px; }}
QComboBox QAbstractItemView {{
    background:{theme.SURFACE}; color:{theme.TEXT};
    selection-background-color:{theme.ACCENT};
    border:1px solid {theme.BORDER};
    outline:none;
}}
QCheckBox {{ color:{theme.MUTED}; }}
QPushButton {{
    background:{theme.SURFACE}; color:{theme.TEXT};
    border:1px solid {theme.BORDER}; border-radius:6px; padding:6px 14px;
}}
QPushButton:hover  {{ background:{theme.SURFACE_HI}; }}
QPushButton:pressed{{ background:{theme.BORDER_HI}; }}
QPushButton:checked{{ background:{theme.SURFACE_SEL}; color:{theme.ACCENT}; border:1px solid {theme.ACCENT}; }}
QPushButton:disabled{{ background:{theme.PANEL}; color:{theme.BORDER_HI}; border-color:{theme.BORDER}; }}
"""


class ChatOptionsDialog(QDialog):
    """Per-chat overrides for model / system prompt / temperature.

    A blank model or system prompt means "inherit the global setting"; the
    temperature is only overridden when its checkbox is ticked.
    """

    def __init__(self, settings, overrides: dict, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._overrides = overrides or {}
        self.setWindowTitle("Chat Options")
        self.setMinimumWidth(520)
        self.setStyleSheet(_style())
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 16)

        intro = QLabel(
            "Overrides apply to this chat only. Leave a field blank to use the "
            "global default from Settings."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{theme.MUTED};font-size:{theme.FS_SM}px;")
        root.addWidget(intro)

        box = QGroupBox("Overrides")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        self.model_edit = QLineEdit(self._overrides.get("model", "") or "")
        self.model_edit.setPlaceholderText(
            f"Default: {self.settings.get('model', '')}"
        )
        form.addRow("Model:", self.model_edit)

        self.temp_check = QCheckBox("Override temperature")
        has_temp = self._overrides.get("temperature") is not None
        self.temp_check.setChecked(has_temp)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.05)
        self.temp_spin.setDecimals(2)
        self.temp_spin.setValue(
            float(self._overrides["temperature"]) if has_temp
            else float(self.settings.get("temperature", 0.7))
        )
        self.temp_spin.setEnabled(has_temp)
        self.temp_check.toggled.connect(self.temp_spin.setEnabled)
        temp_row = QHBoxLayout()
        temp_row.addWidget(self.temp_check)
        temp_row.addWidget(self.temp_spin)
        temp_row.addStretch()
        form.addRow("Temperature:", temp_row)

        self.system_edit = QTextEdit()
        self.system_edit.setPlainText(self._overrides.get("system_prompt", "") or "")
        self.system_edit.setPlaceholderText(
            "Leave blank to use the global system prompt"
        )
        self.system_edit.setFixedHeight(90)
        form.addRow("System prompt:", self.system_edit)

        root.addWidget(box)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Save).setStyleSheet(
            f"background:{theme.ACCENT};color:white;font-weight:600;"
            f"border:none;border-radius:6px;padding:6px 18px;"
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def result_overrides(self) -> dict:
        model = self.model_edit.text().strip()
        system = self.system_edit.toPlainText().strip()
        return {
            "model": model or None,
            "system_prompt": system or None,
            "temperature": (
                round(self.temp_spin.value(), 2) if self.temp_check.isChecked()
                else None
            ),
        }


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._fetcher: _ModelFetcher | None = None
        self._editing_profile = settings.active_profile
        self.setWindowTitle("Settings")
        self.setMinimumWidth(620)
        self.setMinimumHeight(560)
        self.setStyleSheet(_style())
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.setContentsMargins(20, 16, 20, 16)

        # Inline validation error banner (hidden by default)
        self._error_label = QLabel()
        self._error_label.setStyleSheet(
            f"color:{theme.DANGER};background:{theme.DANGER_BG};"
            f"border:1px solid {theme.DANGER_BD};"
            f"border-radius:6px;padding:8px 12px;font-size:{theme.FS_MD}px;"
        )
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        root.addWidget(self._error_label)

        tabs = QTabWidget()
        tabs.setStyleSheet(
            f"QTabBar::tab{{background:{theme.SURFACE};color:{theme.MUTED};"
            f"padding:7px 14px;border:1px solid {theme.BORDER};"
            f"border-bottom:none;border-top-left-radius:6px;"
            f"border-top-right-radius:6px;}}"
            f"QTabBar::tab:selected{{background:{theme.SURFACE_SEL};"
            f"color:{theme.TEXT};}}"
            f"QTabWidget::pane{{border:1px solid {theme.BORDER};"
            f"border-radius:6px;}}"
        )
        tabs.addTab(self._scrolled([self._api_group(), self._param_group()]),
                    "Provider")
        tabs.addTab(self._scrolled([self._memory_group(), self._system_group()]),
                    "Memory && Prompt")
        tabs.addTab(self._scrolled([self._tools_group()]), "Tools")
        tabs.addTab(self._scrolled([self._advanced_group()]), "Advanced")
        root.addWidget(tabs)
        root.addWidget(self._button_box())

    def _scrolled(self, boxes: list) -> QWidget:
        """Put a tab's group boxes into a scroll area so the dialog stays compact."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(10)
        for box in boxes:
            lay.addWidget(box)
        lay.addStretch()
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        area.setWidget(page)
        return area

    def _api_group(self) -> QGroupBox:
        box = QGroupBox("API Configuration")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(10)

        profile_row = QHBoxLayout()
        profile_row.setSpacing(6)
        self.profile_combo = QComboBox()
        self.profile_combo.addItems(self.settings.profile_names())
        self.profile_combo.setCurrentText(self.settings.active_profile)
        self.profile_combo.currentTextChanged.connect(self._on_profile_changed)
        profile_row.addWidget(self.profile_combo, stretch=1)
        for label, slot, tip in (
            ("New", self._new_profile, "Create another provider profile"),
            ("Rename", self._rename_profile, "Rename this profile"),
            ("Delete", self._delete_profile, "Delete this profile"),
        ):
            btn = QPushButton(label)
            btn.setToolTip(tip)
            btn.setFixedWidth(72)
            btn.clicked.connect(slot)
            profile_row.addWidget(btn)
        form.addRow("Profile:", profile_row)

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
        self._status_label.setStyleSheet(
            f"color:{theme.FAINT};font-size:{theme.FS_SM}px;"
        )
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

        self.memory_budget_spin = QSpinBox()
        self.memory_budget_spin.setRange(0, 1_000_000)
        self.memory_budget_spin.setSingleStep(512)
        self.memory_budget_spin.setSpecialValueText("off")
        self.memory_budget_spin.setSuffix(" tokens")
        self.memory_budget_spin.setToolTip(
            "Trim the oldest messages until the request fits this many tokens. "
            "0 disables the budget and keeps the message-count window only."
        )
        self.memory_budget_spin.setValue(
            int(self.settings.get("memory_max_tokens", 0) or 0)
        )

        form.addRow("", self.memory_enabled_check)
        form.addRow("Window size:", self.window_size_spin)
        form.addRow("Token budget:", self.memory_budget_spin)
        form.addRow("", self.vector_check)
        form.addRow("Retrieve top-k:", self.top_k_spin)
        form.addRow("Embedding model:", self.embed_model_edit)
        form.addRow("Embedding URL:", self.embed_url_edit)

        if not chroma_available():
            note = QLabel(
                "chromadb is not installed — vector retrieval is unavailable. "
                "Install it with: pip install chromadb"
            )
            note.setStyleSheet(f"color:#f59e0b;font-size:{theme.FS_XS}px;")
            note.setWordWrap(True)
            form.addRow("", note)
            self.vector_check.setEnabled(False)
            self.vector_check.setChecked(False)

        return box

    def _tools_group(self) -> QGroupBox:
        box = QGroupBox("Tool Calling")
        lay = QVBoxLayout(box)
        lay.setSpacing(8)

        self.tools_check = QCheckBox(
            "Let the model call tools (built-ins and MCP servers)"
        )
        self.tools_check.setChecked(bool(self.settings.get("tools_enabled", False)))
        lay.addWidget(self.tools_check)

        enabled = set(self.settings.get("tool_builtins") or [])
        self._builtin_checks: dict[str, QCheckBox] = {}
        for name, spec in tool_module.BUILTINS.items():
            label = f"{name} — {spec['description'].splitlines()[0]}"
            if spec.get("network"):
                label += "  (makes network requests)"
            check = QCheckBox(label)
            check.setChecked(name in enabled)
            self._builtin_checks[name] = check
            lay.addWidget(check)

        rounds_row = QHBoxLayout()
        rounds_row.addWidget(QLabel("Max tool rounds per reply:"))
        self.tool_rounds_spin = QSpinBox()
        self.tool_rounds_spin.setRange(1, 20)
        self.tool_rounds_spin.setValue(
            int(self.settings.get("tool_max_rounds", 5) or 5)
        )
        rounds_row.addWidget(self.tool_rounds_spin)
        rounds_row.addStretch()
        lay.addLayout(rounds_row)

        lay.addWidget(QLabel("MCP servers:"))
        self.mcp_list = QListWidget()
        self.mcp_list.setFixedHeight(110)
        self._mcp_servers = [
            dict(s) for s in (self.settings.get("mcp_servers") or [])
            if isinstance(s, dict)
        ]
        self._refresh_mcp_list()
        lay.addWidget(self.mcp_list)

        btn_row = QHBoxLayout()
        for label, slot in (
            ("Add…", self._add_mcp), ("Edit…", self._edit_mcp),
            ("Remove", self._remove_mcp),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        return box

    def _refresh_mcp_list(self):
        self.mcp_list.clear()
        for server in self._mcp_servers:
            mark = "☑" if server.get("enabled", True) else "☐"
            args = " ".join(server.get("args") or [])
            item = QListWidgetItem(
                f"{mark}  {server.get('name', '')} — "
                f"{server.get('command', '')} {args}".rstrip()
            )
            self.mcp_list.addItem(item)

    def _add_mcp(self):
        dlg = MCPServerDialog({}, self)
        if dlg.exec():
            self._mcp_servers.append(dlg.result_server())
            self._refresh_mcp_list()

    def _edit_mcp(self):
        row = self.mcp_list.currentRow()
        if row < 0:
            return
        dlg = MCPServerDialog(self._mcp_servers[row], self)
        if dlg.exec():
            self._mcp_servers[row] = dlg.result_server()
            self._refresh_mcp_list()

    def _remove_mcp(self):
        row = self.mcp_list.currentRow()
        if row >= 0:
            self._mcp_servers.pop(row)
            self._refresh_mcp_list()

    def _advanced_group(self) -> QGroupBox:
        box = QGroupBox("Transport, Titles & Rendering")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.headers_edit = QTextEdit()
        self.headers_edit.setFixedHeight(70)
        self.headers_edit.setPlaceholderText(
            "One per line, e.g.\nHTTP-Referer: https://example.com\nX-Title: PyQOA"
        )
        self.headers_edit.setPlainText(
            headers_to_text(self.settings.get("request_headers") or {})
        )
        form.addRow("Extra headers:", self.headers_edit)

        self.proxy_edit = QLineEdit(self.settings.get("proxy", ""))
        self.proxy_edit.setPlaceholderText("http://user:pass@host:port  (blank = none)")
        form.addRow("Proxy:", self.proxy_edit)

        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(1, 10)
        self.retries_spin.setSuffix(" attempts")
        self.retries_spin.setValue(int(self.settings.get("max_retries", 3) or 3))
        form.addRow("Retries:", self.retries_spin)

        self.retry_delay_spin = QDoubleSpinBox()
        self.retry_delay_spin.setRange(0.1, 30.0)
        self.retry_delay_spin.setSingleStep(0.5)
        self.retry_delay_spin.setSuffix(" s")
        self.retry_delay_spin.setValue(
            float(self.settings.get("retry_base_delay", 1.0) or 1.0)
        )
        self.retry_delay_spin.setToolTip("First backoff delay; it doubles each retry.")
        form.addRow("Retry backoff:", self.retry_delay_spin)

        self.auto_title_check = QCheckBox("Let the model name new chats")
        self.auto_title_check.setChecked(bool(self.settings.get("auto_title", True)))
        form.addRow("", self.auto_title_check)

        self.title_model_edit = QLineEdit(self.settings.get("auto_title_model", ""))
        self.title_model_edit.setPlaceholderText(
            "Blank = the chat's own model (a cheap model works well here)"
        )
        form.addRow("Title model:", self.title_model_edit)

        self.stream_render_check = QCheckBox(
            "Render Markdown live while the reply streams"
        )
        self.stream_render_check.setChecked(
            bool(self.settings.get("stream_render", True))
        )
        form.addRow("", self.stream_render_check)

        self.attachments_check = QCheckBox("Allow image and file attachments")
        self.attachments_check.setChecked(
            bool(self.settings.get("attachments_enabled", True))
        )
        form.addRow("", self.attachments_check)

        self.keyring_check = QCheckBox(
            "Store API keys in the OS keyring instead of settings.json"
        )
        self.keyring_check.setChecked(bool(self.settings.get("use_keyring", False)))
        if not keystore.available():
            self.keyring_check.setEnabled(False)
            self.keyring_check.setChecked(False)
            self.keyring_check.setText(
                "OS keyring unavailable — keys stay in settings.json"
            )
        form.addRow("", self.keyring_check)
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
            f"background:{theme.ACCENT};color:white;font-weight:600;"
            f"border:none;border-radius:6px;padding:6px 18px;"
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        return btns


    def _current_profile_values(self) -> dict:
        return {
            "api_url": self.url_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "model": self.model_combo.currentText().strip(),
            "timeout": self.timeout_spin.value(),
            "max_tokens": self.max_tokens_spin.value(),
            "temperature": self.temp_spin.value(),
            "stream": self.stream_check.isChecked(),
            "request_headers": text_to_headers(self.headers_edit.toPlainText()),
            "proxy": self.proxy_edit.text().strip(),
        }

    def _on_profile_changed(self, name: str):
        """Switch the edited profile, keeping unsaved edits to the outgoing one."""
        if not name or name == self._editing_profile:
            return
        self.settings.upsert_profile(
            self._editing_profile, self._current_profile_values()
        )
        profile = self.settings.get_profile(name)
        self._editing_profile = name
        if not profile:
            return
        self.url_edit.setText(profile.get("api_url") or "")
        self.key_edit.setText(profile.get("api_key") or "")
        self.model_combo.setCurrentText(profile.get("model") or "")
        self.timeout_spin.setValue(int(profile.get("timeout") or 60))
        self.max_tokens_spin.setValue(int(profile.get("max_tokens") or 4096))
        self.temp_spin.setValue(float(profile.get("temperature") or 0.7))
        self.stream_check.setChecked(bool(profile.get("stream", True)))
        self.headers_edit.setPlainText(
            headers_to_text(profile.get("request_headers") or {})
        )
        self.proxy_edit.setText(profile.get("proxy") or "")
        self._sync_preset_buttons()

    def _new_profile(self):
        name, ok = QInputDialog.getText(self, "New profile", "Profile name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if name in self.settings.profile_names():
            QMessageBox.warning(self, "Name in use", f"'{name}' already exists.")
            return
        self.settings.upsert_profile(
            self._editing_profile, self._current_profile_values()
        )
        self.settings.upsert_profile(name, self._current_profile_values())
        self.profile_combo.addItem(name)
        self.profile_combo.setCurrentText(name)

    def _rename_profile(self):
        old = self.profile_combo.currentText()
        name, ok = QInputDialog.getText(
            self, "Rename profile", "New name:", text=old
        )
        name = (name or "").strip()
        if not ok or not name or name == old:
            return
        if not self.settings.rename_profile(old, name):
            QMessageBox.warning(self, "Cannot rename", f"'{name}' is already in use.")
            return
        self._editing_profile = name
        self.profile_combo.blockSignals(True)
        self.profile_combo.setItemText(self.profile_combo.currentIndex(), name)
        self.profile_combo.blockSignals(False)

    def _delete_profile(self):
        name = self.profile_combo.currentText()
        if len(self.settings.profile_names()) <= 1:
            QMessageBox.information(
                self, "Cannot delete", "At least one profile must remain."
            )
            return
        if QMessageBox.question(
            self, "Delete profile", f"Delete the profile '{name}'?"
        ) != QMessageBox.StandardButton.Yes:
            return
        if not self.settings.delete_profile(name):
            return
        index = self.profile_combo.currentIndex()
        self.profile_combo.blockSignals(True)
        self.profile_combo.removeItem(index)
        self.profile_combo.blockSignals(False)
        self._editing_profile = ""
        self._on_profile_changed(self.profile_combo.currentText())

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

        self._fetcher = _ModelFetcher(
            url,
            self.key_edit.text().strip(),
            text_to_headers(self.headers_edit.toPlainText()),
            parent=self,
        )
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
        self._status_label.setStyleSheet(f"color:{color};font-size:{theme.FS_SM}px;")
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

        if url and not is_local_url(url) and not key:
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
                "memory_max_tokens":  self.memory_budget_spin.value(),
                "tools_enabled":   self.tools_check.isChecked(),
                "tool_builtins":   [
                    n for n, c in self._builtin_checks.items() if c.isChecked()
                ],
                "tool_max_rounds": self.tool_rounds_spin.value(),
                "mcp_servers":     self._mcp_servers,
                "request_headers": text_to_headers(
                    self.headers_edit.toPlainText()
                ),
                "proxy":            self.proxy_edit.text().strip(),
                "max_retries":      self.retries_spin.value(),
                "retry_base_delay": self.retry_delay_spin.value(),
                "auto_title":       self.auto_title_check.isChecked(),
                "auto_title_model": self.title_model_edit.text().strip(),
                "stream_render":    self.stream_render_check.isChecked(),
                "attachments_enabled": self.attachments_check.isChecked(),
                "use_keyring":      self.keyring_check.isChecked(),
            }
        )
        # Persist the edits into the profile they belong to, then make it active.
        self.settings.upsert_profile(
            self._editing_profile, self._current_profile_values()
        )
        self.settings.activate_profile(self._editing_profile, persist=False)
        self.settings.save()
        self.accept()


class MCPServerDialog(QDialog):
    """Add or edit one MCP server entry (command, args, env)."""

    def __init__(self, server: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("MCP server")
        self.setMinimumWidth(480)
        self.setStyleSheet(_style())
        self._server = dict(server or {})

        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self.name_edit = QLineEdit(self._server.get("name", ""))
        self.name_edit.setPlaceholderText("filesystem")
        form.addRow("Name:", self.name_edit)

        self.command_edit = QLineEdit(self._server.get("command", ""))
        self.command_edit.setPlaceholderText("npx")
        form.addRow("Command:", self.command_edit)

        self.args_edit = QLineEdit(" ".join(self._server.get("args") or []))
        self.args_edit.setPlaceholderText("-y @modelcontextprotocol/server-filesystem /tmp")
        form.addRow("Arguments:", self.args_edit)

        self.env_edit = QTextEdit()
        self.env_edit.setFixedHeight(60)
        self.env_edit.setPlaceholderText("KEY: value  (one per line)")
        self.env_edit.setPlainText(headers_to_text(self._server.get("env") or {}))
        form.addRow("Environment:", self.env_edit)

        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(bool(self._server.get("enabled", True)))
        form.addRow("", self.enabled_check)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    def result_server(self) -> dict:
        return {
            "name": self.name_edit.text().strip() or "mcp",
            "command": self.command_edit.text().strip(),
            "args": self.args_edit.text().split(),
            "env": text_to_headers(self.env_edit.toPlainText()),
            "enabled": self.enabled_check.isChecked(),
        }


class PromptLibraryDialog(QDialog):
    """Saved prompts: insert one into the composer, or manage the list."""

    prompt_chosen = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Prompt library")
        self.setMinimumSize(560, 420)
        self.setStyleSheet(_style())
        self._prompts = [
            dict(p) for p in (settings.get("prompts") or []) if isinstance(p, dict)
        ]

        root = QVBoxLayout(self)
        root.setSpacing(10)

        body = QHBoxLayout()
        self.list_widget = QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_selected)
        self.list_widget.itemDoubleClicked.connect(lambda _: self._insert())
        body.addWidget(self.list_widget, stretch=1)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Prompt text…")
        self.text_edit.textChanged.connect(self._on_text_edited)
        body.addWidget(self.text_edit, stretch=2)
        root.addLayout(body)

        btn_row = QHBoxLayout()
        for label, slot in (
            ("New", self._new), ("Rename", self._rename), ("Delete", self._delete),
        ):
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()

        insert_btn = QPushButton("Insert into message")
        insert_btn.setStyleSheet(
            f"background:{theme.ACCENT};color:white;font-weight:600;"
            f"border:none;border-radius:6px;padding:6px 18px;"
        )
        insert_btn.clicked.connect(self._insert)
        btn_row.addWidget(insert_btn)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self._save_and_close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        self._refresh()

    def _refresh(self, select: int = 0):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for prompt in self._prompts:
            self.list_widget.addItem(prompt.get("name", "Untitled"))
        self.list_widget.blockSignals(False)
        if self._prompts:
            self.list_widget.setCurrentRow(min(select, len(self._prompts) - 1))
        else:
            self.text_edit.clear()

    def _on_selected(self, row: int):
        if 0 <= row < len(self._prompts):
            self.text_edit.blockSignals(True)
            self.text_edit.setPlainText(self._prompts[row].get("text", ""))
            self.text_edit.blockSignals(False)

    def _on_text_edited(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._prompts):
            self._prompts[row]["text"] = self.text_edit.toPlainText()

    def _new(self):
        name, ok = QInputDialog.getText(self, "New prompt", "Name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        self._prompts.append({"name": name, "text": ""})
        self._refresh(len(self._prompts) - 1)
        self.text_edit.setFocus()

    def _rename(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        name, ok = QInputDialog.getText(
            self, "Rename prompt", "Name:", text=self._prompts[row].get("name", "")
        )
        name = (name or "").strip()
        if ok and name:
            self._prompts[row]["name"] = name
            self._refresh(row)

    def _delete(self):
        row = self.list_widget.currentRow()
        if row < 0:
            return
        self._prompts.pop(row)
        self._refresh(max(0, row - 1))

    def _insert(self):
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._prompts):
            self.prompt_chosen.emit(self._prompts[row].get("text", ""))
            self._save_and_close()

    def _save_and_close(self):
        self.settings.set("prompts", self._prompts)
        self.settings.save()
        self.accept()
