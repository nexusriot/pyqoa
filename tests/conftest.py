import os
import sys
from pathlib import Path

import pytest

# Qt must run without a display in CI and over SSH.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from database import Database          # noqa: E402
from memory import ChatMemory          # noqa: E402
from settings import Settings          # noqa: E402


@pytest.fixture
def data_dir(tmp_path) -> Path:
    d = tmp_path / "profile"
    d.mkdir()
    return d


@pytest.fixture
def settings(data_dir) -> Settings:
    return Settings(data_dir)


@pytest.fixture
def db(tmp_path) -> Database:
    database = Database(tmp_path / "chats.db")
    yield database
    database.close()


@pytest.fixture
def memory(settings, db) -> ChatMemory:
    return ChatMemory(settings, db)


@pytest.fixture
def chat(db) -> int:
    return db.create_chat("Test chat")
