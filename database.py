import sqlite3
from pathlib import Path


# Bumped whenever the on-disk layout changes in a way that needs a one-off
# migration step (currently: rebuilding the full-text index).
SCHEMA_VERSION = 2


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 MATCH expression.

    Every token is quoted (so punctuation can't be read as FTS syntax) and the
    last one gets a prefix wildcard, which makes search feel live as you type.
    """
    tokens = [t for t in text.split() if t.strip()]
    if not tokens:
        return ""
    parts = []
    for i, tok in enumerate(tokens):
        quoted = '"' + tok.replace('"', '""') + '"'
        if i == len(tokens) - 1:
            quoted += "*"
        parts.append(quoted)
    return " AND ".join(parts)


class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.fts_enabled = False
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
                temperature   REAL,
                pinned        INTEGER NOT NULL DEFAULT 0,
                archived      INTEGER NOT NULL DEFAULT 0,
                folder        TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id           INTEGER NOT NULL,
                role              TEXT    NOT NULL,
                content           TEXT    NOT NULL,
                prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                completion_tokens INTEGER NOT NULL DEFAULT 0,
                created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
                model             TEXT,
                variant_group     INTEGER,
                active            INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                kind       TEXT    NOT NULL,
                name       TEXT    NOT NULL,
                mime       TEXT    NOT NULL DEFAULT '',
                data       BLOB,
                created_at TEXT    NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE
            );
        """)
        self._conn.commit()
        # Migrations: add columns to databases created by older versions.
        for table, col_def in (
            ("messages", "prompt_tokens INTEGER NOT NULL DEFAULT 0"),
            ("messages", "completion_tokens INTEGER NOT NULL DEFAULT 0"),
            ("messages", "model TEXT"),
            ("messages", "variant_group INTEGER"),
            ("messages", "active INTEGER NOT NULL DEFAULT 1"),
            ("chats",    "model TEXT"),
            ("chats",    "system_prompt TEXT"),
            ("chats",    "temperature REAL"),
            ("chats",    "pinned INTEGER NOT NULL DEFAULT 0"),
            ("chats",    "archived INTEGER NOT NULL DEFAULT 0"),
            ("chats",    "folder TEXT"),
        ):
            try:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
        # Indexes come after the migrations: some of them cover columns that an
        # older database only gains in the loop above.
        self._conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_messages_chat
                ON messages(chat_id, id);
            CREATE INDEX IF NOT EXISTS idx_messages_variant
                ON messages(variant_group);
            CREATE INDEX IF NOT EXISTS idx_attachments_message
                ON attachments(message_id);
            CREATE INDEX IF NOT EXISTS idx_chats_sort
                ON chats(archived, pinned, updated_at);
        """)
        self._conn.commit()
        self._init_fts()

    def _init_fts(self):
        """Create the FTS5 mirror of message content, if this SQLite has FTS5.

        The table is an *external content* index over `messages`, kept in sync by
        triggers, so message text is stored once. Without FTS5 the app falls back
        to a LIKE scan (see `search_messages`).
        """
        try:
            self._conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content, content='messages', content_rowid='id'
                );

                CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages
                BEGIN
                    INSERT INTO messages_fts(rowid, content)
                    VALUES (new.id, new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages
                BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                END;

                CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE OF content
                ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES ('delete', old.id, old.content);
                    INSERT INTO messages_fts(rowid, content)
                    VALUES (new.id, new.content);
                END;
            """)
            # Backfill rows written before the index existed. `COUNT(*)` on an
            # external-content table reads through to `messages`, so it can't
            # detect the gap — a stamped schema version can.
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                self._conn.execute(
                    "INSERT INTO messages_fts(messages_fts) VALUES('rebuild')"
                )
                self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            self._conn.commit()
            self.fts_enabled = True
        except sqlite3.Error:
            self.fts_enabled = False

    def create_chat(self, title: str = "New Chat", folder: str | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO chats (title, folder) VALUES (?, ?)", (title, folder)
        )
        self._conn.commit()
        return cur.lastrowid

    _CHAT_COLS = (
        "id, title, created_at, updated_at, model, system_prompt, temperature, "
        "pinned, archived, folder"
    )

    def get_chats(self, include_archived: bool = False) -> list:
        """Chats in sidebar order: pinned first, then most recently updated."""
        where = "" if include_archived else "WHERE archived=0"
        return self._conn.execute(
            f"SELECT {self._CHAT_COLS} FROM chats {where} "
            "ORDER BY pinned DESC, updated_at DESC, id DESC"
        ).fetchall()

    def get_chat(self, chat_id: int):
        """Return a single chat row (including per-chat overrides), or None."""
        return self._conn.execute(
            f"SELECT {self._CHAT_COLS} FROM chats WHERE id=?", (chat_id,)
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

    def set_pinned(self, chat_id: int, pinned: bool):
        self._conn.execute(
            "UPDATE chats SET pinned=? WHERE id=?", (1 if pinned else 0, chat_id)
        )
        self._conn.commit()

    def set_archived(self, chat_id: int, archived: bool):
        self._conn.execute(
            "UPDATE chats SET archived=? WHERE id=?", (1 if archived else 0, chat_id)
        )
        self._conn.commit()

    def set_folder(self, chat_id: int, folder: str | None):
        """Move a chat into a folder. Blank/None puts it back at the top level."""
        self._conn.execute(
            "UPDATE chats SET folder=? WHERE id=?", (folder or None, chat_id)
        )
        self._conn.commit()

    def folders(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT folder FROM chats "
            "WHERE folder IS NOT NULL AND folder<>'' ORDER BY folder COLLATE NOCASE"
        ).fetchall()
        return [r[0] for r in rows]

    def delete_chat(self, chat_id: int):
        self._conn.execute("DELETE FROM chats WHERE id=?", (chat_id,))
        self._conn.commit()

    def delete_chats(self, chat_ids: list[int]):
        if not chat_ids:
            return
        marks = ",".join("?" * len(chat_ids))
        self._conn.execute(f"DELETE FROM chats WHERE id IN ({marks})", chat_ids)
        self._conn.commit()

    def branch_chat(
        self, chat_id: int, message_id: int, title: str | None = None
    ) -> int:
        """Copy a chat up to and including `message_id` into a brand-new chat.

        This is the non-destructive counterpart to edit/regenerate: the original
        conversation is left untouched and the copy becomes a separate branch.
        """
        src = self.get_chat(chat_id)
        if src is None:
            raise ValueError(f"chat {chat_id} not found")
        keys = src.keys()
        new_title = title or f"{src['title']} (branch)"
        cur = self._conn.execute(
            "INSERT INTO chats (title, model, system_prompt, temperature, folder) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                new_title,
                src["model"] if "model" in keys else None,
                src["system_prompt"] if "system_prompt" in keys else None,
                src["temperature"] if "temperature" in keys else None,
                src["folder"] if "folder" in keys else None,
            ),
        )
        new_id = cur.lastrowid
        rows = self._conn.execute(
            "SELECT id, role, content, prompt_tokens, completion_tokens, model "
            "FROM messages WHERE chat_id=? AND active=1 AND id<=? ORDER BY id ASC",
            (chat_id, message_id),
        ).fetchall()
        for row in rows:
            cur = self._conn.execute(
                "INSERT INTO messages "
                "(chat_id, role, content, prompt_tokens, completion_tokens, model) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_id, row["role"], row["content"],
                    row["prompt_tokens"], row["completion_tokens"], row["model"],
                ),
            )
            self._copy_attachments(row["id"], cur.lastrowid)
        self._conn.commit()
        return new_id

    def _copy_attachments(self, src_message_id: int, dst_message_id: int):
        for att in self._conn.execute(
            "SELECT kind, name, mime, data FROM attachments WHERE message_id=?",
            (src_message_id,),
        ).fetchall():
            self._conn.execute(
                "INSERT INTO attachments (message_id, kind, name, mime, data) "
                "VALUES (?, ?, ?, ?, ?)",
                (dst_message_id, att["kind"], att["name"], att["mime"], att["data"]),
            )

    def add_message(
        self,
        chat_id: int,
        role: str,
        content: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        model: str | None = None,
        variant_group: int | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO messages "
            "(chat_id, role, content, prompt_tokens, completion_tokens, model, "
            " variant_group) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, role, content, prompt_tokens, completion_tokens,
             model, variant_group),
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

    def get_message(self, message_id: int):
        return self._conn.execute(
            "SELECT id, chat_id, role, content, prompt_tokens, completion_tokens, "
            "created_at, model, variant_group, active FROM messages WHERE id=?",
            (message_id,),
        ).fetchone()

    def delete_messages_from(self, chat_id: int, message_id: int) -> list[int]:
        """Delete the given message and every message after it in the chat.

        Ordering is by `id` (insertion order), so `id >= message_id` is exactly
        "this message and everything that came after". Inactive siblings of any
        deleted variant go too, so no orphaned alternates are left behind.
        Returns the ids that were removed (the vector store needs them).
        """
        ids = [
            r[0] for r in self._conn.execute(
                "SELECT id FROM messages WHERE chat_id=? AND (id>=? OR "
                "(variant_group IS NOT NULL AND variant_group IN "
                " (SELECT variant_group FROM messages "
                "  WHERE chat_id=? AND id>=? AND variant_group IS NOT NULL)))",
                (chat_id, message_id, chat_id, message_id),
            ).fetchall()
        ]
        if ids:
            marks = ",".join("?" * len(ids))
            self._conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", ids)
            self.touch_chat(chat_id)
            self._conn.commit()
        return ids

    def truncate_after(self, chat_id: int, message_id: int) -> list[int]:
        """Delete every message *after* `message_id` (it is kept)."""
        ids = [
            r[0] for r in self._conn.execute(
                "SELECT id FROM messages WHERE chat_id=? AND id>?",
                (chat_id, message_id),
            ).fetchall()
        ]
        if ids:
            marks = ",".join("?" * len(ids))
            self._conn.execute(f"DELETE FROM messages WHERE id IN ({marks})", ids)
            self.touch_chat(chat_id)
            self._conn.commit()
        return ids

    def begin_variant(self, message_id: int) -> int:
        """Deactivate a message and return the variant group it now belongs to.

        The first regenerate of a reply seeds the group with the reply's own id,
        so every alternate answer to the same prompt shares one group id.
        """
        row = self.get_message(message_id)
        if row is None:
            raise ValueError(f"message {message_id} not found")
        group = row["variant_group"] or message_id
        self._conn.execute(
            "UPDATE messages SET active=0, variant_group=? WHERE id=?",
            (group, message_id),
        )
        self._conn.commit()
        return group

    def get_variants(self, message_id: int) -> list:
        """All alternates in this message's variant group, oldest first."""
        row = self.get_message(message_id)
        if row is None or row["variant_group"] is None:
            return []
        return self._conn.execute(
            "SELECT id, role, content, prompt_tokens, completion_tokens, model, active "
            "FROM messages WHERE variant_group=? ORDER BY id ASC",
            (row["variant_group"],),
        ).fetchall()

    def set_active_variant(self, message_id: int) -> bool:
        """Make one alternate the visible one, hiding its siblings."""
        row = self.get_message(message_id)
        if row is None or row["variant_group"] is None:
            return False
        self._conn.execute(
            "UPDATE messages SET active=0 WHERE variant_group=?",
            (row["variant_group"],),
        )
        self._conn.execute(
            "UPDATE messages SET active=1 WHERE id=?", (message_id,)
        )
        self._conn.commit()
        return True

    _MSG_COLS = (
        "id, chat_id, role, content, prompt_tokens, completion_tokens, "
        "created_at, model, variant_group, active"
    )

    def get_messages(self, chat_id: int) -> list:
        """The visible conversation: active messages only, in insertion order."""
        return self._conn.execute(
            f"SELECT {self._MSG_COLS} FROM messages "
            "WHERE chat_id=? AND active=1 ORDER BY id ASC",
            (chat_id,),
        ).fetchall()

    def get_chat_token_totals(self, chat_id: int) -> tuple[int, int]:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0) "
            "FROM messages WHERE chat_id=? AND active=1",
            (chat_id,),
        ).fetchone()
        return row[0], row[1]

    def add_attachment(
        self, message_id: int, kind: str, name: str, mime: str, data: bytes | None
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO attachments (message_id, kind, name, mime, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (message_id, kind, name, mime, data),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_attachments(self, message_id: int) -> list:
        return self._conn.execute(
            "SELECT id, message_id, kind, name, mime, data FROM attachments "
            "WHERE message_id=? ORDER BY id ASC",
            (message_id,),
        ).fetchall()

    def search_messages(
        self, query: str, limit: int = 60, include_archived: bool = False
    ) -> list[dict]:
        """Full-text search over message bodies.

        Uses FTS5 when available (with a highlighted snippet) and degrades to a
        LIKE scan otherwise, so search always works.
        """
        query = (query or "").strip()
        if not query:
            return []
        arch = "" if include_archived else " AND c.archived=0"
        if self.fts_enabled:
            match = _fts_query(query)
            if not match:
                return []
            try:
                rows = self._conn.execute(
                    "SELECT m.id AS message_id, m.chat_id AS chat_id, m.role AS role, "
                    "       c.title AS title, "
                    "       snippet(messages_fts, 0, '«', '»', '…', 12) AS snippet "
                    "FROM messages_fts f "
                    "JOIN messages m ON m.id = f.rowid "
                    "JOIN chats c ON c.id = m.chat_id "
                    f"WHERE messages_fts MATCH ? AND m.active=1{arch} "
                    "ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
                return [dict(r) for r in rows]
            except sqlite3.Error:
                pass  # malformed MATCH — fall through to LIKE
        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT m.id AS message_id, m.chat_id AS chat_id, m.role AS role, "
            "       c.title AS title, substr(m.content, 1, 160) AS snippet "
            "FROM messages m JOIN chats c ON c.id = m.chat_id "
            f"WHERE m.content LIKE ? AND m.active=1{arch} "
            "ORDER BY m.id DESC LIMIT ?",
            (like, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_chats(self, query: str, include_archived: bool = False) -> list:
        """Title-only search (the sidebar's cheap filter)."""
        arch = "" if include_archived else " AND archived=0"
        return self._conn.execute(
            f"SELECT {self._CHAT_COLS} FROM chats "
            f"WHERE title LIKE ?{arch} "
            "ORDER BY pinned DESC, updated_at DESC, id DESC",
            (f"%{query}%",),
        ).fetchall()

    def usage_totals(self) -> tuple[int, int, int]:
        """(messages, prompt_tokens, completion_tokens) across the whole database."""
        row = self._conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), "
            "COALESCE(SUM(completion_tokens),0) FROM messages WHERE active=1"
        ).fetchone()
        return row[0], row[1], row[2]

    def usage_by_day(self, days: int = 30) -> list:
        return self._conn.execute(
            "SELECT date(created_at) AS day, "
            "       COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "       COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "       COUNT(*) AS messages "
            "FROM messages WHERE active=1 AND created_at >= datetime('now', ?) "
            "GROUP BY day ORDER BY day ASC",
            (f"-{int(days)} days",),
        ).fetchall()

    def usage_by_model(self) -> list:
        """Per-model totals; rows saved before models were recorded group as ''."""
        return self._conn.execute(
            "SELECT COALESCE(model, '') AS model, "
            "       COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "       COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "       COUNT(*) AS messages "
            "FROM messages WHERE active=1 "
            "GROUP BY COALESCE(model, '') "
            "ORDER BY (SUM(prompt_tokens) + SUM(completion_tokens)) DESC"
        ).fetchall()

    def close(self):
        self._conn.close()
