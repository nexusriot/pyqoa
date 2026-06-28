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
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                title         TEXT    NOT NULL DEFAULT 'New Chat',
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                model         TEXT,
                system_prompt TEXT,
                temperature   REAL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id           INTEGER NOT NULL,
                role              TEXT    NOT NULL,
                content           TEXT    NOT NULL,
                prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );
        """)
        self._conn.commit()
        # Migrations: add columns to databases created by older versions.
        for table, col_def in (
            ("messages", "prompt_tokens INTEGER NOT NULL DEFAULT 0"),
            ("messages", "completion_tokens INTEGER NOT NULL DEFAULT 0"),
            ("chats",    "model TEXT"),
            ("chats",    "system_prompt TEXT"),
            ("chats",    "temperature REAL"),
        ):
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists


    def create_chat(self, title: str = "New Chat") -> int:
        cur = self._conn.execute(
            "INSERT INTO chats (title) VALUES (?)", (title,)
        )
        self._conn.commit()
        return cur.lastrowid

    def get_chats(self) -> list:
        return self._conn.execute(
            "SELECT id, title, created_at, updated_at "
            "FROM chats ORDER BY updated_at DESC, id DESC"
        ).fetchall()

    def get_chat(self, chat_id: int):
        """Return a single chat row (including per-chat overrides), or None."""
        return self._conn.execute(
            "SELECT id, title, created_at, updated_at, model, system_prompt, temperature "
            "FROM chats WHERE id=?",
            (chat_id,),
        ).fetchone()

    def update_chat_overrides(
        self,
        chat_id: int,
        model: str | None,
        system_prompt: str | None,
        temperature: float | None,
    ):
        """Set per-chat overrides. Pass None to inherit the global setting."""
        self._conn.execute(
            "UPDATE chats SET model=?, system_prompt=?, temperature=? WHERE id=?",
            (model, system_prompt, temperature, chat_id),
        )
        self._conn.commit()

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


    def add_message(
        self,
        chat_id: int,
        role: str,
        content: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO messages (chat_id, role, content, prompt_tokens, completion_tokens) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, role, content, prompt_tokens, completion_tokens),
        )
        self.touch_chat(chat_id)
        self._conn.commit()
        return cur.lastrowid

    def update_message_tokens(
        self, message_id: int, prompt_tokens: int, completion_tokens: int
    ):
        self._conn.execute(
            "UPDATE messages SET prompt_tokens=?, completion_tokens=? WHERE id=?",
            (prompt_tokens, completion_tokens, message_id),
        )
        self._conn.commit()

    def delete_messages_from(self, chat_id: int, message_id: int):
        """Delete the given message and every message after it in the chat.

        Ordering is by `id` (insertion order), so `id >= message_id` is exactly
        "this message and everything that came after". Used by edit/regenerate.
        """
        self._conn.execute(
            "DELETE FROM messages WHERE chat_id=? AND id>=?",
            (chat_id, message_id),
        )
        self.touch_chat(chat_id)
        self._conn.commit()

    def get_messages(self, chat_id: int) -> list:
        return self._conn.execute(
            "SELECT id, chat_id, role, content, prompt_tokens, completion_tokens, created_at "
            "FROM messages WHERE chat_id=? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()

    def get_chat_token_totals(self, chat_id: int) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            "FROM messages WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        return row[0], row[1]

    def close(self):
        self._conn.close()
