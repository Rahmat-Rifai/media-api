import secrets
from typing import Optional, Dict, Any, List
import sqlite3

def gen_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"

class ClientRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_client(self, name: str, api_key_hash: str, daily_bytes_quota: int, concurrency_limit: int = 1) -> Dict[str, Any]:
        client_id = gen_id("cli")
        with self.conn:
            self.conn.execute(
                "INSERT INTO clients (id, name, api_key_hash, daily_bytes_quota, concurrency_limit) VALUES (?, ?, ?, ?, ?)",
                (client_id, name, api_key_hash, daily_bytes_quota, concurrency_limit)
            )
        return self.get_by_id(client_id)

    def get_by_id(self, client_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_by_api_key_hash(self, api_key_hash: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM clients WHERE api_key_hash = ? AND is_active = 1", (api_key_hash,))
        row = cur.fetchone()
        return dict(row) if row else None

    def increment_daily_usage(self, client_id: str, bytes_count: int):
        with self.conn:
            self.conn.execute(
                "UPDATE clients SET daily_bytes_used = daily_bytes_used + ? WHERE id = ?",
                (bytes_count, client_id)
            )

    def get_active_task_count(self, client_id: str) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE client_id = ? AND status IN ('preparing', 'running')",
            (client_id,)
        )
        return cur.fetchone()[0]

class TaskRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_task(self, client_id: str, url: str, format_id: Optional[str] = None, max_height: Optional[int] = None, audio_only: bool = False) -> Dict[str, Any]:
        task_id = gen_id("tsk")
        with self.conn:
            self.conn.execute(
                """INSERT INTO tasks (id, client_id, url, status, format_id, max_height, audio_only)
                   VALUES (?, ?, ?, 'queued', ?, ?, ?)""",
                (task_id, client_id, url, format_id, max_height, 1 if audio_only else 0)
            )
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def update_status(self, task_id: str, status: str, error_code: Optional[str] = None, error_message: Optional[str] = None):
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status = ?, error_code = ?, error_message = ? WHERE id = ?",
                (status, error_code, error_message, task_id)
            )

    def update_progress(self, task_id: str, progress: float):
        with self.conn:
            self.conn.execute("UPDATE tasks SET progress = ? WHERE id = ?", (progress, task_id))

    def set_reservation(self, task_id: str, estimated_bytes: Optional[int], reserved_bytes: int):
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET estimated_bytes = ?, reserved_bytes = ?, status = 'running' WHERE id = ?",
                (estimated_bytes, reserved_bytes, task_id)
            )

    def complete_task(self, task_id: str, actual_bytes: int):
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status = 'done', progress = 100.0, actual_bytes = ?, reserved_bytes = 0 WHERE id = ?",
                (actual_bytes, task_id)
            )

    def reset_running_tasks_on_startup(self) -> int:
        with self.conn:
            cur = self.conn.execute(
                """UPDATE tasks SET status = 'failed', error_code = 'system_restart', error_message = 'Process terminated unexpectedly during restart'
                   WHERE status IN ('preparing', 'running')"""
            )
            return cur.rowcount

class FileRepository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def create_file(self, task_id: str, client_id: str, filename: str, original_title: str, mime_type: str, size_bytes: int, storage_path: str, expires_at: str) -> Dict[str, Any]:
        file_id = gen_id("fl")
        with self.conn:
            self.conn.execute(
                """INSERT INTO files (id, task_id, client_id, filename, original_title, mime_type, size_bytes, storage_path, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (file_id, task_id, client_id, filename, original_title, mime_type, size_bytes, storage_path, expires_at)
            )
        return self.get_file(file_id)

    def get_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,))
        row = cur.fetchone()
        return dict(row) if row else None

    def get_files_by_task(self, task_id: str) -> List[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM files WHERE task_id = ?", (task_id,))
        return [dict(r) for r in cur.fetchall()]

    def update_last_accessed(self, file_id: str):
        with self.conn:
            self.conn.execute("UPDATE files SET last_accessed_at = CURRENT_TIMESTAMP WHERE id = ?", (file_id,))

    def get_total_storage_used(self) -> int:
        cur = self.conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM files")
        return cur.fetchone()[0]

    def list_evictable_files(self, grace_period_seconds: int) -> List[Dict[str, Any]]:
        cur = self.conn.execute(
            """SELECT * FROM files
               WHERE (unixepoch() - unixepoch(last_accessed_at)) > ?
                  OR unixepoch(expires_at) <= unixepoch()
               ORDER BY last_accessed_at ASC""",
            (grace_period_seconds,)
        )
        return [dict(r) for r in cur.fetchall()]

    def list_expired_or_evictable(self, grace_period_seconds: int) -> List[Dict[str, Any]]:
        return self.list_evictable_files(grace_period_seconds)

    def delete_file(self, file_id: str):
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))