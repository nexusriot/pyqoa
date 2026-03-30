import sqlite3
from pathlib import Path


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL DEFAULT 'New Chat',
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id     INTEGER NOT NULL,
                role        TEXT    NOT NULL,
                content     TEXT    NOT NULL,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );
        """)
        self._conn.commit()


    def create_chat(self, title: str = "New Chat") -> int:
        cur = self._conn.execute(
            "INSERT INTO chats (title) VALUES (?)", (title,)
        )
        self._conn.commit()
        return cur.lastrowid

    def get_chats(self) -> list:
        return self._conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM chats ORDER BY updated_at DESC"
        ).fetchall()

    def update_chat_title(self, chat_id: int, title: str):
        self._conn.execute(
            "UPDATE chats SET title=?, updated_at=datetime('now') WHERE id=?",
            (title, chat_id),
        )
        self._conn.commit()

    def touch_chat(self, chat_id: int):
        self._conn.execute(
            "UPDATE chats SET updated_at=datetime('now') WHERE id=?", (chat_id,)
        )
        self._conn.commit()

    def delete_chat(self, chat_id: int):
        self._conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        self._conn.commit()


    def add_message(self, chat_id: int, role: str, content: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, content),
        )
        self.touch_chat(chat_id)
        self._conn.commit()
        return cur.lastrowid

    def get_messages(self, chat_id: int) -> list:
        return self._conn.execute(
            "SELECT id, chat_id, role, content, created_at "
            "FROM messages WHERE chat_id=? ORDER BY created_at ASC",
            (chat_id,),
        ).fetchall()

    def close(self):
        self._conn.close()
