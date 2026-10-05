import unittest
import os
import tempfile
import asyncio
from unittest.mock import patch, MagicMock
from app.core.worker import WorkerService
from app.core.storage import StorageManager
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository, TaskRepository, FileRepository


class TestWorker(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "media.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)
        self.client_repo = ClientRepository(self.conn)
        self.task_repo = TaskRepository(self.conn)
        self.file_repo = FileRepository(self.conn)
        self.storage_mgr = StorageManager(self.temp_dir.name)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_worker_cancellation(self):
        client = self.client_repo.create_client("c", "h", 100000)
        task = self.task_repo.create_task(client["id"], "https://soundcloud.com/test")

        worker = WorkerService(
            task_repo=self.task_repo,
            file_repo=self.file_repo,
            client_repo=self.client_repo,
            storage_mgr=self.storage_mgr,
        )
        worker.cancel_task(task["id"])
        updated = self.task_repo.get_task(task["id"])
        self.assertEqual(updated["status"], "cancelled")

    def test_worker_execution_success(self):
        client = self.client_repo.create_client("c", "h", 1000000)
        task = self.task_repo.create_task(client["id"], "https://soundcloud.com/sample/track")

        worker = WorkerService(
            task_repo=self.task_repo,
            file_repo=self.file_repo,
            client_repo=self.client_repo,
            storage_mgr=self.storage_mgr,
        )

        dummy_file_path = os.path.join(self.storage_mgr.get_tmp_dir(task["id"]), "track.mp3")
        with open(dummy_file_path, "wb") as f:
            f.write(b"fake audio data" * 100)

        with patch.object(
            worker.ytdlp_engine,
            "extract_info",
            return_value={"title": "Track", "estimated_bytes": 1000},
        ), patch.object(worker.ytdlp_engine, "download", return_value=[dummy_file_path]):
            asyncio.run(worker.execute_task(task["id"]))

        completed_task = self.task_repo.get_task(task["id"])
        self.assertEqual(completed_task["status"], "done")
        self.assertEqual(completed_task["progress"], 100.0)

        files = self.file_repo.get_files_by_task(task["id"])
        self.assertEqual(len(files), 1)
        self.assertTrue(os.path.exists(files[0]["storage_path"]))


if __name__ == "__main__":
    unittest.main()

