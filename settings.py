import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Any

import keystore

DEFAULTS: dict = {
    "api_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o",
    "timeout": 60,
    "max_tokens": 4096,
    "temperature": 0.7,
    "system_prompt": "You are a helpful assistant.",
    "stream": True,
    "theme": "dark",  # "system", "dark", or "light"
    "font_scale": 1.0,

    # Provider profiles. `profiles` holds one dict per named provider; the
    # active profile's fields are mirrored into the top level above so every
    # existing `settings.get("api_url")` call site keeps working.
    "profiles": [],
    "active_profile": "Default",
    "use_keyring": False,

    # Transport
    "request_headers": {},  # extra HTTP headers (OpenRouter, Azure, gateways)
    "proxy": "",            # http(s) proxy URL; blank = system default
    "max_retries": 3,
    "retry_base_delay": 1.0,

    "memory_enabled": True,
    "memory_window_size": 20,
    "memory_max_tokens": 0,  # 0 = no token budget, message count only
    "memory_use_vector": False,
    "memory_top_k": 4,
    "memory_embed_model": "text-embedding-3-small",
    "memory_embed_url": "",  # blank = use api_url
    "memory_embed_key": "",  # blank = use api_key

    # Titles
    "auto_title": True,
    "auto_title_model": "",  # blank = the chat's effective model

    # Tools
    "tools_enabled": False,
    "tool_builtins": ["current_time", "calculate"],
    "tool_max_rounds": 5,
    "mcp_servers": [],  # [{name, command, args, env, enabled}]

    # Prompt library: [{"name": str, "text": str}]
    "prompts": [],

    # Rendering
    "stream_render": True,   # incremental Markdown while streaming
    "attachments_enabled": True,

    # UI state (base64-encoded Qt save blobs; blank = use defaults)
    "window_geometry": "",
    "splitter_state": "",
    "show_archived": False,
}

# Fields that belong to a provider profile rather than the app as a whole.
PROFILE_KEYS = (
    "api_url", "api_key", "model", "timeout", "max_tokens",
    "temperature", "stream", "request_headers", "proxy",
)


def _profile_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path.home() / "AppData" / "Roaming"
    elif system == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    return base / "pyqoa"


class Settings:
    def __init__(self, data_dir: Path | None = None):
        self._dir = Path(data_dir) if data_dir else _profile_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "settings.json"
        self._data: dict = dict(DEFAULTS)
        self._load()
        self._ensure_profiles()

    @property
    def profile_dir(self) -> Path:
        return self._dir

    @property
    def db_path(self) -> Path:
        return self._dir / "chats.db"

    @property
    def path(self) -> Path:
        return self._path

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._data.update(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        """Persist settings atomically so a crash can never truncate the file."""
        self.sync_active_profile()
        data = dict(self._data)
        if self._data.get("use_keyring") and keystore.available():
            # Secrets live in the OS keyring; scrub them from the JSON copy.
            data = self._scrub_secrets(data)
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=str(self._dir), prefix=".settings-", suffix=".tmp"
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _scrub_secrets(self, data: dict) -> dict:
        """Move API keys into the keyring and blank them in the JSON payload."""
        data = dict(data)
        active = data.get("active_profile") or "Default"
        keystore.set_key(keystore.account_for(active), data.get("api_key", ""))
        data["api_key"] = ""
        profiles = []
        for prof in data.get("profiles", []):
            prof = dict(prof)
            keystore.set_key(
                keystore.account_for(prof.get("name", "")), prof.get("api_key", "")
            )
            prof["api_key"] = ""
            profiles.append(prof)
        data["profiles"] = profiles
        return data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value

    def update(self, data: dict):
        self._data.update(data)

    def as_dict(self) -> dict:
        return dict(self._data)

    def _ensure_profiles(self):
        """Guarantee at least one profile exists and mirrors the active values."""
        profiles = self._data.get("profiles") or []
        if not isinstance(profiles, list):
            profiles = []
        if not profiles:
            name = self._data.get("active_profile") or "Default"
            profiles = [{"name": name, **{k: self._data.get(k) for k in PROFILE_KEYS}}]
        self._data["profiles"] = profiles
        names = [p.get("name") for p in profiles]
        if self._data.get("active_profile") not in names:
            self._data["active_profile"] = names[0]
        if self._data.get("use_keyring"):
            self._restore_secrets()
        self.activate_profile(self._data["active_profile"], persist=False)

    def _restore_secrets(self):
        """Pull API keys back out of the OS keyring after a scrubbed save."""
        if not keystore.available():
            return
        for prof in self._data.get("profiles", []):
            if not prof.get("api_key"):
                prof["api_key"] = keystore.get_key(
                    keystore.account_for(prof.get("name", ""))
                )

    def profiles(self) -> list[dict]:
        return list(self._data.get("profiles") or [])

    def profile_names(self) -> list[str]:
        return [p.get("name", "") for p in self.profiles()]

    @property
    def active_profile(self) -> str:
        return self._data.get("active_profile") or "Default"

    def get_profile(self, name: str) -> dict | None:
        for prof in self.profiles():
            if prof.get("name") == name:
                return dict(prof)
        return None

    def sync_active_profile(self):
        """Copy the live top-level provider fields back into the active profile."""
        name = self.active_profile
        for prof in self._data.get("profiles") or []:
            if prof.get("name") == name:
                for key in PROFILE_KEYS:
                    prof[key] = self._data.get(key)
                return
        self._data.setdefault("profiles", []).append(
            {"name": name, **{k: self._data.get(k) for k in PROFILE_KEYS}}
        )

    def activate_profile(self, name: str, persist: bool = True) -> bool:
        """Make `name` the active profile, mirroring its fields to the top level."""
        target = None
        for prof in self._data.get("profiles") or []:
            if prof.get("name") == name:
                target = prof
                break
        if target is None:
            return False
        if persist and self.active_profile != name:
            self.sync_active_profile()  # don't lose edits to the outgoing profile
        self._data["active_profile"] = name
        for key in PROFILE_KEYS:
            if key in target and target[key] is not None:
                self._data[key] = target[key]
        if persist:
            self.save()
        return True

    def upsert_profile(self, name: str, values: dict) -> None:
        """Create or update a named profile (does not change the active one)."""
        for prof in self._data.setdefault("profiles", []):
            if prof.get("name") == name:
                prof.update({k: v for k, v in values.items() if k in PROFILE_KEYS})
                break
        else:
            base = {k: self._data.get(k) for k in PROFILE_KEYS}
            base.update({k: v for k, v in values.items() if k in PROFILE_KEYS})
            self._data["profiles"].append({"name": name, **base})
        if name == self.active_profile:
            for key in PROFILE_KEYS:
                if key in values:
                    self._data[key] = values[key]

    def rename_profile(self, old: str, new: str) -> bool:
        if not new or old == new or new in self.profile_names():
            return False
        for prof in self._data.get("profiles") or []:
            if prof.get("name") == old:
                prof["name"] = new
                if self._data.get("active_profile") == old:
                    self._data["active_profile"] = new
                if self._data.get("use_keyring"):
                    keystore.set_key(
                        keystore.account_for(new), prof.get("api_key", "")
                    )
                    keystore.delete_key(keystore.account_for(old))
                return True
        return False

    def delete_profile(self, name: str) -> bool:
        """Remove a profile. The last remaining profile cannot be deleted."""
        profiles = self._data.get("profiles") or []
        if len(profiles) <= 1:
            return False
        remaining = [p for p in profiles if p.get("name") != name]
        if len(remaining) == len(profiles):
            return False
        self._data["profiles"] = remaining
        if self._data.get("use_keyring"):
            keystore.delete_key(keystore.account_for(name))
        if self._data.get("active_profile") == name:
            self.activate_profile(remaining[0]["name"], persist=False)
        return True
