from __future__ import annotations
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConversationStore:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "assistant.sqlite3"
        self._initialize()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _initialize(self):
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新しい会話', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('user', 'assistant')), content TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id)")

    def create_conversation(self, title="新しい会話"):
        cid, now = str(uuid.uuid4()), _now()
        with self._connect() as conn:
            conn.execute("INSERT INTO conversations VALUES (?, ?, ?, ?)", (cid, title, now, now))
        return cid

    def ensure_conversation(self, cid):
        if cid:
            with self._connect() as conn:
                row = conn.execute("SELECT id FROM conversations WHERE id=?", (cid,)).fetchone()
            if row:
                return cid
        return self.create_conversation()

    def add_message(self, cid, role, content):
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        now = _now()
        with self._connect() as conn:
            conn.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)", (cid, role, content, now))
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
            if role == "user":
                row = conn.execute("SELECT title FROM conversations WHERE id=?", (cid,)).fetchone()
                if row and row["title"] == "新しい会話":
                    title = content.replace("\n", " ").strip()[:36]
                    if title:
                        conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, cid))

    def get_messages(self, cid, limit=16):
        with self._connect() as conn:
            rows = conn.execute("SELECT role, content FROM (SELECT id, role, content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?) AS recent ORDER BY id ASC", (cid, limit)).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def get_history(self, cid):
        with self._connect() as conn:
            rows = conn.execute("SELECT role, content, created_at FROM messages WHERE conversation_id=? ORDER BY id", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def last_user_message(self, cid):
        with self._connect() as conn:
            row = conn.execute("SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
        return row["content"] if row else None

    def remove_last_assistant(self, cid):
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (cid,)).fetchone()
            if row:
                conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))

    def list_conversations(self, limit=50):
        with self._connect() as conn:
            rows = conn.execute("SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
