import unittest
import os
import tempfile
import sqlite3
from app.db.database import init_db, get_db_connection, get_db_path
from app.db.repositories import ClientRepository, TaskRepository, FileRepository
from app.core.config import settings

class TestDatabaseAndRepos(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")
        init_db(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_client_crud_and_quota(self):
        conn = get_db_connection(self.db_path)
        client_repo = ClientRepository(conn)
        client = client_repo.create_client(
            name="Test Client",
            api_key_hash="hash123",
            daily_bytes_quota=1000,
            concurrency_limit=2
        )
        self.assertEqual(client["name"], "Test Client")
        self.assertEqual(client["daily_bytes_used"], 0)
        self.assertEqual(client["concurrency_limit"], 2)

        client_repo.increment_daily_usage(client["id"], 500)
        updated = client_repo.get_by_id(client["id"])
        self.assertEqual(updated["daily_bytes_used"], 500)

        found_by_hash = client_repo.get_by_api_key_hash("hash123")
        self.assertIsNotNone(found_by_hash)
        self.assertEqual(found_by_hash["id"], client["id"])

        not_found = client_repo.get_by_api_key_hash("nonexistent")
        self.assertIsNone(not_found)
        conn.close()

    def test_task_lifecycle_and_startup_reset(self):
        conn = get_db_connection(self.db_path)
        client_repo = ClientRepository(conn)
        task_repo = TaskRepository(conn)
        client = client_repo.create_client(name="c", api_key_hash="h", daily_bytes_quota=1000)

        task = task_repo.create_task(client["id"], "https://soundcloud.com/track")
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["progress"], 0.0)

        self.assertEqual(client_repo.get_active_task_count(client["id"]), 0)

        task_repo.update_status(task["id"], "running")
        running_task = task_repo.get_task(task["id"])
        self.assertEqual(running_task["status"], "running")
        self.assertEqual(client_repo.get_active_task_count(client["id"]), 1)

        task_repo.update_progress(task["id"], 45.5)
        task_in_prog = task_repo.get_task(task["id"])
        self.assertEqual(task_in_prog["progress"], 45.5)

        task_repo.set_reservation(task["id"], estimated_bytes=5000, reserved_bytes=5000)
        task_res = task_repo.get_task(task["id"])
        self.assertEqual(task_res["estimated_bytes"], 5000)
        self.assertEqual(task_res["reserved_bytes"], 5000)
        self.assertEqual(task_res["status"], "running")

        task_repo.complete_task(task["id"], actual_bytes=4800)
        completed = task_repo.get_task(task["id"])
        self.assertEqual(completed["status"], "done")
        self.assertEqual(completed["progress"], 100.0)
        self.assertEqual(completed["actual_bytes"], 4800)
        self.assertEqual(completed["reserved_bytes"], 0)
        self.assertEqual(client_repo.get_active_task_count(client["id"]), 0)

        t2 = task_repo.create_task(client["id"], "https://soundcloud.com/track2")
        task_repo.update_status(t2["id"], "running")
        t3 = task_repo.create_task(client["id"], "https://soundcloud.com/track3")
        task_repo.update_status(t3["id"], "preparing")

        reset_count = task_repo.reset_running_tasks_on_startup()
        self.assertEqual(reset_count, 2)
        recovered_t2 = task_repo.get_task(t2["id"])
        self.assertEqual(recovered_t2["status"], "failed")
        self.assertEqual(recovered_t2["error_code"], "system_restart")
        recovered_t3 = task_repo.get_task(t3["id"])
        self.assertEqual(recovered_t3["status"], "failed")
        self.assertEqual(recovered_t3["error_code"], "system_restart")
        conn.close()

    def test_file_repository_operations(self):
        conn = get_db_connection(self.db_path)
        client_repo = ClientRepository(conn)
        task_repo = TaskRepository(conn)
        file_repo = FileRepository(conn)

        client = client_repo.create_client(name="fc", api_key_hash="fh", daily_bytes_quota=50000)
        task = task_repo.create_task(client["id"], "https://soundcloud.com/file-track")

        expires_at = "2026-10-06 12:00:00"
        file_record = file_repo.create_file(
            task_id=task["id"],
            client_id=client["id"],
            filename="output.mp3",
            original_title="File Track",
            mime_type="audio/mpeg",
            size_bytes=1024,
            storage_path="/storage/output.mp3",
            expires_at=expires_at
        )

        self.assertEqual(file_record["filename"], "output.mp3")
        self.assertEqual(file_record["size_bytes"], 1024)

        fetched = file_repo.get_file(file_record["id"])
        self.assertEqual(fetched["id"], file_record["id"])

        files = file_repo.get_files_by_task(task["id"])
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]["id"], file_record["id"])

        total_storage = file_repo.get_total_storage_used()
        self.assertEqual(total_storage, 1024)

        file_repo.update_last_accessed(file_record["id"])
        updated_f = file_repo.get_file(file_record["id"])
        self.assertIsNotNone(updated_f["last_accessed_at"])

        past_expires = "2020-01-01 00:00:00"
        file_record_exp = file_repo.create_file(
            task_id=task["id"],
            client_id=client["id"],
            filename="expired.mp3",
            original_title="Expired Track",
            mime_type="audio/mpeg",
            size_bytes=2048,
            storage_path="/storage/expired.mp3",
            expires_at=past_expires
        )
        evictable = file_repo.list_evictable_files(grace_period_seconds=86400)
        evictable_ids = [f["id"] for f in evictable]
        self.assertIn(file_record_exp["id"], evictable_ids)

        alias_evictable = file_repo.list_expired_or_evictable(grace_period_seconds=86400)
        self.assertEqual(len(alias_evictable), len(evictable))

        file_repo.delete_file(file_record["id"])
        self.assertIsNone(file_repo.get_file(file_record["id"]))
        self.assertEqual(file_repo.get_total_storage_used(), 2048)
        conn.close()

    def test_database_pragmas_and_constraints(self):
        conn = get_db_connection(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")

        cur.execute("PRAGMA foreign_keys;")
        foreign_keys = cur.fetchone()[0]
        self.assertEqual(foreign_keys, 1)

        cur.execute("PRAGMA busy_timeout;")
        busy_timeout = cur.fetchone()[0]
        self.assertEqual(busy_timeout, 5000)

        cur.execute("PRAGMA synchronous;")
        synchronous = cur.fetchone()[0]
        self.assertEqual(synchronous, 1)

        # Foreign key enforcement check
        with self.assertRaises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    "INSERT INTO tasks (id, client_id, url, status) VALUES ('t1', 'nonexistent_client', 'http://a', 'queued')"
                )

        # Test CASCADE delete on files
        client_repo = ClientRepository(conn)
        task_repo = TaskRepository(conn)
        file_repo = FileRepository(conn)
        c = client_repo.create_client(name="fk_c", api_key_hash="fk_h", daily_bytes_quota=100)
        t = task_repo.create_task(c["id"], "https://soundcloud.com/fk")
        f = file_repo.create_file(t["id"], c["id"], "f.mp3", "fk", "audio/mpeg", 100, "/tmp/f.mp3", "2099-01-01 00:00:00")
        self.assertIsNotNone(file_repo.get_file(f["id"]))

        with conn:
            conn.execute("DELETE FROM tasks WHERE id = ?", (t["id"],))
        self.assertIsNone(file_repo.get_file(f["id"]))

        conn.close()

    def test_get_db_path_default(self):
        default_path = get_db_path()
        expected = os.path.join(settings.DATA_PATH, "media.db")
        self.assertEqual(default_path, expected)

if __name__ == "__main__":
    unittest.main()