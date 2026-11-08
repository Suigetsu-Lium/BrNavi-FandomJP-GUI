from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConversationStore:
    SCHEMA_VERSION = 1

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "assistant.sqlite3"
        self.backup_path = None
        self._initialize()

    @contextmanager
    def _connect_path(self, path, *, uri=False):
        conn = sqlite3.connect(path, timeout=30, uri=uri)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            conn.close()
            raise RuntimeError("SQLite foreign key enforcement could not be enabled.")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _connect(self):
        return self._connect_path(self.db_path)

    @staticmethod
    def _columns(conn, table):
        return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}

    def _backup_database(self):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.data_dir / f"assistant-pre-migration-{stamp}.sqlite3"
        suffix = 1
        while target.exists():
            target = self.data_dir / f"assistant-pre-migration-{stamp}-{suffix}.sqlite3"
            suffix += 1

        with self._connect() as source, self._connect_path(target) as destination:
            source.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"Database backup integrity check failed: {check}")
        self.backup_path = target

    def _initialize(self):
        existed = self.db_path.exists()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS conversations ("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '新しい会話', "
                "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, "
                "role TEXT NOT NULL CHECK(role IN ('user', 'assistant')), "
                "content TEXT NOT NULL, created_at TEXT NOT NULL, "
                "FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conversation "
                "ON messages(conversation_id, id)"
            )
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise RuntimeError(
                    f"Conversation database schema {version} is newer than supported "
                    f"schema {self.SCHEMA_VERSION}."
                )
            if version == self.SCHEMA_VERSION:
                return

        if existed:
            self._backup_database()
        self._migrate_to_current()

    def _migrate_to_current(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conversation_columns = self._columns(conn, "conversations")
                if "title_source" not in conversation_columns:
                    conn.execute(
                        "ALTER TABLE conversations ADD COLUMN title_source TEXT "
                        "NOT NULL DEFAULT 'legacy' "
                        "CHECK(title_source IN ('auto', 'manual', 'legacy'))"
                    )
                    conn.execute(
                        "UPDATE conversations SET title_source='auto' "
                        "WHERE title='新しい会話'"
                    )

                message_columns = self._columns(conn, "messages")
                if "current_version_number" not in message_columns:
                    conn.execute(
                        "ALTER TABLE messages ADD COLUMN current_version_number INTEGER"
                    )

                conn.execute(
                    "CREATE TABLE IF NOT EXISTS message_versions ("
                    "message_id INTEGER NOT NULL, "
                    "version_number INTEGER NOT NULL CHECK(version_number >= 1), "
                    "content TEXT NOT NULL, created_at TEXT NOT NULL, "
                    "rating TEXT CHECK(rating IS NULL OR rating IN ('good', 'bad')), "
                    "sources_json TEXT NOT NULL DEFAULT '[]', "
                    "PRIMARY KEY(message_id, version_number), "
                    "FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE)"
                )
                if "sources_json" not in self._columns(conn, "message_versions"):
                    conn.execute(
                        "ALTER TABLE message_versions ADD COLUMN sources_json TEXT "
                        "NOT NULL DEFAULT '[]'"
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO message_versions "
                    "(message_id, version_number, content, created_at, rating) "
                    "SELECT id, 1, content, created_at, NULL FROM messages "
                    "WHERE role='assistant'"
                )
                conn.execute(
                    "UPDATE messages SET current_version_number=1 "
                    "WHERE role='assistant' AND current_version_number IS NULL"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS generation_requests ("
                    "request_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
                    "message_id INTEGER, operation TEXT NOT NULL "
                    "CHECK(operation IN ('send', 'regenerate')), "
                    "status TEXT NOT NULL CHECK(status IN "
                    "('running', 'cancel_requested', 'completed', 'failed', 'cancelled')), "
                    "started_at TEXT NOT NULL, finished_at TEXT, "
                    "FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE, "
                    "FOREIGN KEY(message_id) REFERENCES messages(id) ON DELETE CASCADE)"
                )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "idx_one_active_generation_per_conversation "
                    "ON generation_requests(conversation_id) "
                    "WHERE status IN ('running', 'cancel_requested')"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS validate_current_answer_version "
                    "BEFORE UPDATE OF current_version_number ON messages "
                    "WHEN NEW.role='assistant' AND NEW.current_version_number IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM message_versions "
                    "WHERE message_id=NEW.id "
                    "AND version_number=NEW.current_version_number) "
                    "BEGIN SELECT RAISE(ABORT, 'current answer version does not exist'); END"
                )
                conn.execute(
                    "CREATE TRIGGER IF NOT EXISTS sync_current_answer_content "
                    "AFTER UPDATE OF current_version_number ON messages "
                    "WHEN NEW.role='assistant' AND NEW.current_version_number IS NOT NULL "
                    "BEGIN UPDATE messages SET content=(SELECT content FROM message_versions "
                    "WHERE message_id=NEW.id AND version_number=NEW.current_version_number) "
                    "WHERE id=NEW.id; END"
                )
                conn.execute("PRAGMA user_version = 1")
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"Foreign key violations found: {violations!r}")
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def create_conversation(self, title="新しい会話"):
        cid, now = str(uuid.uuid4()), _now()
        title_source = "auto" if title == "新しい会話" else "manual"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations "
                "(id, title, created_at, updated_at, title_source) VALUES (?, ?, ?, ?, ?)",
                (cid, title, now, now, title_source),
            )
        return cid

    def ensure_conversation(self, cid):
        if cid:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE id=?", (str(cid),)
                ).fetchone()
            if row:
                return str(cid)
        return self.create_conversation()

    def begin_generation(
        self, cid, message, operation="send", message_id=None, request_id=None
    ):
        if operation not in {"send", "regenerate"}:
            raise ValueError("Unsupported generation operation.")
        request_id, now = request_id or str(uuid.uuid4()), _now()
        cid = self.ensure_conversation(cid)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                user_message_id = None
                target_message_id = message_id
                prompt = message
                should_generate_title = False
                if operation == "send":
                    should_generate_title = (
                        conn.execute(
                            "SELECT COUNT(*) FROM messages WHERE conversation_id=?",
                            (cid,),
                        ).fetchone()[0]
                        == 0
                    )
                    cursor = conn.execute(
                        "INSERT INTO messages(conversation_id, role, content, created_at) "
                        "VALUES (?, 'user', ?, ?)",
                        (cid, message, now),
                    )
                    user_message_id = cursor.lastrowid
                    title = conn.execute(
                        "SELECT title, title_source FROM conversations WHERE id=?", (cid,)
                    ).fetchone()
                    if title and title["title_source"] == "auto" and title["title"] == "新しい会話":
                        fallback = " ".join(message.split())[:36]
                        if fallback:
                            conn.execute(
                                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                                (fallback, now, cid),
                            )
                else:
                    if target_message_id is None:
                        row = conn.execute(
                            "SELECT id FROM messages WHERE conversation_id=? "
                            "AND role='assistant' ORDER BY id DESC LIMIT 1",
                            (cid,),
                        ).fetchone()
                        target_message_id = row["id"] if row else None
                    latest = conn.execute(
                        "SELECT id, role FROM messages WHERE conversation_id=? "
                        "ORDER BY id DESC LIMIT 1",
                        (cid,),
                    ).fetchone()
                    if (
                        not latest
                        or latest["role"] != "assistant"
                        or latest["id"] != target_message_id
                    ):
                        raise ValueError("再生成できる最新の回答がありません。")
                    row = conn.execute(
                        "SELECT content FROM messages WHERE id=? AND conversation_id=? "
                        "AND role='assistant'",
                        (target_message_id, cid),
                    ).fetchone()
                    if not row:
                        raise ValueError("再生成できる回答がありません。")
                    prompt_row = conn.execute(
                        "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
                        "AND id < ? ORDER BY id DESC LIMIT 1",
                        (cid, target_message_id),
                    ).fetchone()
                    if not prompt_row:
                        raise ValueError("再生成できるユーザーメッセージがありません。")
                    prompt = prompt_row["content"]

                conn.execute(
                    "INSERT INTO generation_requests "
                    "(request_id, conversation_id, message_id, operation, status, started_at) "
                    "VALUES (?, ?, ?, ?, 'running', ?)",
                    (request_id, cid, target_message_id, operation, now),
                )
                conn.execute(
                    "UPDATE conversations SET updated_at=? WHERE id=?", (now, cid)
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {
            "request_id": request_id,
            "conversation_id": cid,
            "operation": operation,
            "user_message_id": user_message_id,
            "message_id": target_message_id,
            "prompt": prompt,
            "should_generate_title": should_generate_title,
        }

    def mark_cancel_requested(self, request_id):
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE generation_requests SET status='cancel_requested' "
                "WHERE request_id=? AND status='running'",
                (request_id,),
            )
            return cursor.rowcount == 1

    def finish_generation(self, request_id, content, sources=None):
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                request = conn.execute(
                    "SELECT conversation_id, message_id, operation, status "
                    "FROM generation_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if not request or request["status"] != "running":
                    conn.rollback()
                    return None

                message_id = request["message_id"]
                version_number = None
                if request["operation"] == "regenerate":
                    version_number = conn.execute(
                        "SELECT COALESCE(MAX(version_number), 0) + 1 "
                        "FROM message_versions WHERE message_id=?",
                        (message_id,),
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO message_versions "
                        "(message_id, version_number, content, created_at, sources_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            message_id,
                            version_number,
                            content,
                            now,
                            json.dumps(sources or [], ensure_ascii=False),
                        ),
                    )
                else:
                    cursor = conn.execute(
                        "INSERT INTO messages(conversation_id, role, content, created_at) "
                        "VALUES (?, 'assistant', ?, ?)",
                        (request["conversation_id"], content, now),
                    )
                    message_id = cursor.lastrowid
                    version_number = 1
                    conn.execute(
                        "INSERT INTO message_versions "
                        "(message_id, version_number, content, created_at, sources_json) "
                        "VALUES (?, 1, ?, ?, ?)",
                        (
                            message_id,
                            content,
                            now,
                            json.dumps(sources or [], ensure_ascii=False),
                        ),
                    )

                conn.execute(
                    "UPDATE messages SET current_version_number=? WHERE id=?",
                    (version_number, message_id),
                )
                conn.execute(
                    "UPDATE generation_requests SET status='completed', message_id=?, "
                    "finished_at=? WHERE request_id=? AND status='running'",
                    (message_id, now, request_id),
                )
                conn.execute(
                    "UPDATE conversations SET updated_at=? WHERE id=?",
                    (now, request["conversation_id"]),
                )
                conn.commit()
                return {"message_id": message_id, "version_number": version_number}
            except Exception:
                conn.rollback()
                raise

    def finish_generation_error(self, request_id, cancelled=False):
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE generation_requests SET status=?, finished_at=? "
                "WHERE request_id=? AND status IN ('running', 'cancel_requested')",
                ("cancelled" if cancelled else "failed", now, request_id),
            )

    def add_message(self, cid, role, content):
        if role not in {"user", "assistant"}:
            raise ValueError("role must be user or assistant")
        if role == "user":
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO messages(conversation_id, role, content, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (cid, role, content, _now()),
                )
                return cursor.lastrowid
        raise ValueError("Assistant messages must be saved as generated versions.")

    def get_messages(self, cid, limit=16):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM (SELECT id, role, content FROM messages "
                "WHERE conversation_id=? ORDER BY id DESC LIMIT ?) AS recent ORDER BY id ASC",
                (cid, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def get_history(self, cid):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, role, content, created_at, current_version_number "
                "FROM messages WHERE conversation_id=? ORDER BY id",
                (cid,),
            ).fetchall()
            history = []
            for row in rows:
                item = dict(row)
                if item["role"] == "assistant":
                    versions = conn.execute(
                        "SELECT version_number, content, created_at, rating, sources_json "
                        "FROM message_versions WHERE message_id=? ORDER BY version_number",
                        (item["id"],),
                    ).fetchall()
                    item["versions"] = [dict(version) for version in versions]
                    for version in item["versions"]:
                        version["sources"] = json.loads(version.pop("sources_json"))
                    current = next(
                        (
                            version
                            for version in item["versions"]
                            if version["version_number"] == item["current_version_number"]
                        ),
                        None,
                    )
                    item["sources"] = current["sources"] if current else []
                history.append(item)
        return history

    def last_user_message(self, cid):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' "
                "ORDER BY id DESC LIMIT 1",
                (cid,),
            ).fetchone()
        return row["content"] if row else None

    def set_current_version(self, message_id, version_number):
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE messages SET current_version_number=? "
                "WHERE id=? AND role='assistant' AND EXISTS ("
                "SELECT 1 FROM message_versions WHERE message_id=? AND version_number=?)",
                (version_number, message_id, message_id, version_number),
            )
            return cursor.rowcount == 1

    def set_rating(self, message_id, version_number, rating):
        if rating not in {None, "good", "bad"}:
            raise ValueError("Rating must be good, bad, or null.")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE message_versions SET rating=? "
                "WHERE message_id=? AND version_number=?",
                (rating, message_id, version_number),
            )
            return cursor.rowcount == 1

    def rename_conversation(self, cid, title):
        title = " ".join(str(title).split()).strip()
        if not title or len(title) > 80:
            raise ValueError("会話名は1～80文字で指定してください。")
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET title=?, title_source='manual', updated_at=? "
                "WHERE id=?",
                (title, _now(), cid),
            )
            return cursor.rowcount == 1

    def generate_title(self, cid, title):
        title = " ".join(str(title).split()).strip()[:80]
        if not title:
            return False
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE conversations SET title=?, updated_at=? "
                "WHERE id=? AND title_source='auto'",
                (title, _now(), cid),
            )
            return cursor.rowcount == 1

    def delete_conversation(self, cid):
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
            return cursor.rowcount == 1

    def get_active_request(self, cid):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_id, operation, status FROM generation_requests "
                "WHERE conversation_id=? AND status IN ('running', 'cancel_requested') "
                "ORDER BY started_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
        return dict(row) if row else None

    def get_generation(self, request_id):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM generation_requests WHERE request_id=?", (request_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(self, limit=50):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, title_source, created_at, updated_at "
                "FROM conversations ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
