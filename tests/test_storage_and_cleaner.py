import unittest
import os
import tempfile
from app.core.storage import StorageManager
from app.core.cleaner import CleanerService
from app.core.errors import AppException, ErrorCode
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository, TaskRepository, FileRepository


class TestStorageAndCleaner(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_dir = os.path.join(self.temp_dir.name, "storage")
        self.data_dir = os.path.join(self.temp_dir.name, "data")
        os.makedirs(self.storage_dir)
        os.makedirs(self.data_dir)
        self.db_path = os.path.join(self.data_dir, "media.db")
        init_db(self.db_path)
        self.storage_mgr = StorageManager(base_path=self.storage_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_atomic_file_commit(self):
        task_id = "tsk_test123"
        tmp_dir = self.storage_mgr.get_tmp_dir(task_id)
        test_file = os.path.join(tmp_dir, "source.mp3")
        with open(test_file, "wb") as f:
            f.write(b"dummy audio content")

        final_path = self.storage_mgr.commit_file_atomically(test_file, "fl_final123.mp3")
        self.assertTrue(os.path.exists(final_path))
        self.assertFalse(os.path.exists(test_file))

    def test_atomic_file_commit_nonexistent_source(self):
        with self.assertRaises(AppException) as ctx:
            self.storage_mgr.commit_file_atomically("/nonexistent/missing.mp3", "dst.mp3")
        self.assertEqual(ctx.exception.code, ErrorCode.INTERNAL_ERROR)

    def test_cleaner_eviction(self):
        conn = get_db_connection(self.db_path)
        client_repo = ClientRepository(conn)
        task_repo = TaskRepository(conn)
        file_repo = FileRepository(conn)

        client = client_repo.create_client("c", "h", 100000)
        task = task_repo.create_task(client["id"], "https://soundcloud.com/t")

        files_dir = os.path.join(self.storage_dir, "files")
        os.makedirs(files_dir, exist_ok=True)
        file_path = os.path.join(files_dir, "fl_evict.mp3")
        with open(file_path, "wb") as f:
            f.write(b"x" * 1024)

        file_repo.create_file(
            task_id=task["id"],
            client_id=client["id"],
            filename="fl_evict.mp3",
            original_title="Evict Me",
            mime_type="audio/mpeg",
            size_bytes=1024,
            storage_path=file_path,
            expires_at="2000-01-01 00:00:00",
        )

        cleaner = CleanerService(
            file_repo=file_repo,
            storage_mgr=self.storage_mgr,
            max_bytes=2048,
            low_watermark=1024,
            high_watermark=1500,
        )
        stats = cleaner.run_eviction_cycle(force_emergency=False)
        self.assertEqual(stats["evicted_count"], 0)

        stats = cleaner.run_eviction_cycle(force_emergency=True)
        self.assertEqual(stats["evicted_count"], 1)
        self.assertFalse(os.path.exists(file_path))
        conn.close()

    def test_cleaner_reserve_space_success_and_overflow(self):
        conn = get_db_connection(self.db_path)
        client_repo = ClientRepository(conn)
        task_repo = TaskRepository(conn)
        file_repo = FileRepository(conn)

        client = client_repo.create_client("c2", "h2", 100000)
        task = task_repo.create_task(client["id"], "https://soundcloud.com/t2")

        cleaner = CleanerService(
            file_repo=file_repo,
            storage_mgr=self.storage_mgr,
            max_bytes=1000,
            high_watermark=800,
            low_watermark=500,
        )

        self.assertTrue(cleaner.reserve_space(500))

        with self.assertRaises(AppException) as ctx:
            cleaner.reserve_space(1500)
        self.assertEqual(ctx.exception.code, ErrorCode.QUOTA_EXCEEDED)
        conn.close()


if __name__ == "__main__":
    unittest.main()

