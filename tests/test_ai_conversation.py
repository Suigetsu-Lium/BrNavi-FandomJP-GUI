import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from modules.ai.conversation import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def create_legacy_database(self, directory):
        path = Path(directory) / "assistant.sqlite3"
        conn = sqlite3.connect(path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL "
                "DEFAULT '新しい会話', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "conversation_id TEXT NOT NULL, role TEXT NOT NULL "
                "CHECK(role IN ('user', 'assistant')), content TEXT NOT NULL, "
                "created_at TEXT NOT NULL, FOREIGN KEY(conversation_id) "
                "REFERENCES conversations(id) ON DELETE CASCADE)"
            )
            conn.execute(
                "CREATE INDEX idx_messages_conversation ON messages(conversation_id, id)"
            )
            conn.execute(
                "INSERT INTO conversations VALUES "
                "('auto', '新しい会話', '2025-01-01', '2025-01-01'), "
                "('manual', '手動タイトル', '2025-01-02', '2025-01-02')"
            )
            conn.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES "
                "('auto', 'user', '質問', '2025-01-01'), "
                "('auto', 'assistant', '以前の回答', '2025-01-01'), "
                "('manual', 'user', '別の質問', '2025-01-02'), "
                "('manual', 'assistant', '別の回答', '2025-01-02')"
            )
            conn.commit()
        finally:
            conn.close()
        return path

    def test_wal_backup_migration_preserves_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.create_legacy_database(directory)
            store = ConversationStore(directory)

            self.assertTrue(store.backup_path.exists())
            with closing(sqlite3.connect(store.backup_path)) as backup:
                self.assertEqual(
                    backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 4
                )
                self.assertNotIn(
                    "title_source",
                    {
                        row[1]
                        for row in backup.execute("PRAGMA table_info(conversations)")
                    },
                )
                backup.close()

            with store._connect() as conn:
                self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(
                    conn.execute(
                        "SELECT title_source FROM conversations WHERE id='auto'"
                    ).fetchone()[0],
                    "auto",
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT title_source FROM conversations WHERE id='manual'"
                    ).fetchone()[0],
                    "legacy",
                )
                self.assertEqual(
                    [tuple(row) for row in conn.execute(
                        "SELECT content FROM messages WHERE role='assistant' ORDER BY id"
                    )],
                    [("以前の回答",), ("別の回答",)],
                )
            history = store.get_history("auto")
            answer = history[1]
            self.assertEqual(answer["current_version_number"], 1)
            self.assertEqual(answer["versions"][0]["content"], "以前の回答")
            self.assertEqual(answer["versions"][0]["rating"], None)
            self.assertTrue(path.exists())

    def test_versions_ratings_and_cascade_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            cid = store.create_conversation()
            generation = store.begin_generation(cid, "質問")
            first = store.finish_generation(generation["request_id"], "回答1")
            second_generation = store.begin_generation(
                cid, "", "regenerate", first["message_id"]
            )
            second = store.finish_generation(second_generation["request_id"], "回答2")

            self.assertEqual(first["version_number"], 1)
            self.assertEqual(second["version_number"], 2)
            self.assertTrue(store.set_rating(first["message_id"], 1, "good"))
            self.assertTrue(store.set_rating(first["message_id"], 2, "bad"))
            self.assertTrue(store.set_current_version(first["message_id"], 1))
            history = store.get_history(cid)[1]
            self.assertEqual(history["content"], "回答1")
            self.assertEqual(
                [version["rating"] for version in history["versions"]], ["good", "bad"]
            )

            with store._connect() as conn:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO message_versions "
                        "(message_id, version_number, content, created_at) "
                        "VALUES (?, 1, 'duplicate', 'now')",
                        (first["message_id"],),
                    )

            self.assertTrue(store.delete_conversation(cid))
            with store._connect() as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM message_versions").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM generation_requests").fetchone()[0],
                    0,
                )

    def test_cancel_and_failure_do_not_replace_prior_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            cid = store.create_conversation()
            send = store.begin_generation(cid, "質問")
            answer = store.finish_generation(send["request_id"], "保持する回答")
            regen = store.begin_generation(cid, "", "regenerate", answer["message_id"])

            self.assertTrue(store.mark_cancel_requested(regen["request_id"]))
            self.assertIsNone(store.finish_generation(regen["request_id"], "破棄する回答"))
            store.finish_generation_error(regen["request_id"], cancelled=True)
            current = store.get_history(cid)[1]
            self.assertEqual(current["content"], "保持する回答")
            self.assertEqual(len(current["versions"]), 1)
            self.assertEqual(
                store.get_generation(regen["request_id"])["status"], "cancelled"
            )

    def test_manual_title_cannot_be_overwritten_by_auto_title(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            cid = store.create_conversation()
            self.assertTrue(store.rename_conversation(cid, "自分で付けた名前"))
            self.assertFalse(store.generate_title(cid, "AIのタイトル"))
            self.assertEqual(store.list_conversations()[0]["title"], "自分で付けた名前")

    def test_only_one_active_generation_per_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(directory)
            cid = store.create_conversation()
            store.begin_generation(cid, "質問")
            with self.assertRaises(sqlite3.IntegrityError):
                store.begin_generation(cid, "重複")
            self.assertEqual(
                store.get_history(cid)[0]["content"], "質問"
            )


if __name__ == "__main__":
    unittest.main()
