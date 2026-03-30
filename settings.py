import json
import platform
from pathlib import Path
from typing import Any

DEFAULTS: dict = {
    "api_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o",
    "timeout": 60,
    "max_tokens": 4096,
    "temperature": 0.7,
    "system_prompt": "You are a helpful assistant.",
    "stream": True,
}


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
    def __init__(self):
        self._dir = _profile_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / "settings.json"
        self._data: dict = dict(DEFAULTS)
        self._load()

    @property
    def profile_dir(self) -> Path:
        return self._dir

    @property
    def db_path(self) -> Path:
        return self._dir / "chats.db"

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path, encoding="utf-8") as f:
                    self._data.update(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass

    def save(self):
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value

    def update(self, data: dict):
        self._data.update(data)

    def as_dict(self) -> dict:
        return dict(self._data)
