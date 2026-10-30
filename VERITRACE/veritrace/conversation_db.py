"""SQLite conversation storage with recoverable deletion."""

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DATABASE = Path(__file__).resolve().parent.parent / "database" / "conversations.db"


class ConversationStore:
    def __init__(self, path=DEFAULT_DATABASE):
        self.path = Path(path)
        self._initialized = False
        self._lock = threading.Lock()

    def initialize(self):
        with self._lock:
            if self._initialized:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), timeout=30)
            migrated = False
            try:
                with conn:
                    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL DEFAULT 'New Conversation',
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        data TEXT NOT NULL DEFAULT '{}',
                        deleted_at INTEGER DEFAULT NULL
                    )""")
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)")}
                    if "deleted_at" not in columns:
                        conn.execute("ALTER TABLE conversations ADD COLUMN deleted_at INTEGER DEFAULT NULL")
                        migrated = True
                    conn.execute(
                        "CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at DESC)"
                    )
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_deleted_at ON conversations(deleted_at)")
            finally:
                conn.close()
            self._initialized = True
            logging.info("[ConversationDB] Initialized; path=%s schema_migrated=%s", self.path.resolve(), migrated)

    @contextmanager
    def _connection(self):
        self.initialize()
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def list_conversations(self, include_deleted=False):
        columns = "id, title, created_at, updated_at" + (", deleted_at" if include_deleted else "")
        condition = "" if include_deleted else "WHERE deleted_at IS NULL"
        with self._connection() as conn:
            return [
                dict(row)
                for row in conn.execute(f"SELECT {columns} FROM conversations {condition} ORDER BY updated_at DESC")
            ]

    def get_conversation(self, conversation_id):
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at, data FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["data"] = json.loads(result["data"])
        except json.JSONDecodeError:
            result["data"] = {}
        return result

    def create_conversation(self, conversation_id, title="New Conversation", data=None):
        now = int(time.time() * 1000)
        data = {} if data is None else data
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at, data) VALUES (?, ?, ?, ?, ?)",
                (conversation_id, title, now, now, json.dumps(data, ensure_ascii=False)),
            )
        logging.info("[ConversationDB] Created conversation; id=%s", conversation_id)
        return {"id": conversation_id, "title": title, "created_at": now, "updated_at": now, "data": data}

    def update_conversation(self, conversation_id, title=None, data=None, skip_timestamp_update=False):
        updates, params = [], []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if data is not None:
            updates.append("data = ?")
            params.append(json.dumps(data, ensure_ascii=False))
        if not skip_timestamp_update:
            updates.append("updated_at = ?")
            params.append(int(time.time() * 1000))
        if not updates:
            return False
        with self._connection() as conn:
            return (
                conn.execute(
                    f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?", params + [conversation_id]
                ).rowcount
                > 0
            )

    def delete_conversation(self, conversation_id):
        with self._connection() as conn:
            affected = conn.execute(
                "UPDATE conversations SET deleted_at = ? WHERE id = ? AND deleted_at IS NULL",
                (int(time.time() * 1000), conversation_id),
            ).rowcount
        if affected:
            logging.info("[ConversationDB] Soft deleted conversation; id=%s", conversation_id)
        return affected > 0

    def get_database_stats(self):
        with self._connection() as conn:
            active, deleted = conn.execute(
                "SELECT COALESCE(SUM(deleted_at IS NULL), 0), COALESCE(SUM(deleted_at IS NOT NULL), 0) FROM conversations"
            ).fetchone()
        size = self.path.stat().st_size
        return {
            "conversation_count": active,
            "deleted_count": deleted,
            "total_count": active + deleted,
            "database_size_bytes": size,
            "database_size_mb": round(size / (1024 * 1024), 2),
            "database_path": str(self.path),
        }

    def list_deleted_conversations(self):
        with self._connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id, title, created_at, updated_at, deleted_at FROM conversations "
                    "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC"
                )
            ]

    def restore_conversation(self, conversation_id):
        with self._connection() as conn:
            affected = conn.execute(
                "UPDATE conversations SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
                (conversation_id,),
            ).rowcount
        if affected:
            logging.info("[ConversationDB] Restored conversation; id=%s", conversation_id)
        return affected > 0

    def permanently_delete_conversation(self, conversation_id):
        with self._connection() as conn:
            affected = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount
        if affected:
            logging.info("[ConversationDB] Permanently deleted conversation; id=%s", conversation_id)
        return affected > 0

    def permanently_delete_all_deleted(self):
        with self._connection() as conn:
            affected = conn.execute("DELETE FROM conversations WHERE deleted_at IS NOT NULL").rowcount
        logging.info("[ConversationDB] Permanently deleted trash; rows=%s", affected)
        return affected
