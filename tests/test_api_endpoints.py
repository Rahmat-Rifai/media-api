import unittest
import hashlib
import tempfile
import os
from starlette.testclient import TestClient
from fastapi import FastAPI
from app.api.endpoints.extract import router as extract_router
from app.api.endpoints.tasks import router as tasks_router
from app.api.endpoints.files import router as files_router
from app.api.endpoints.admin import router as admin_router
from app.api.deps import get_db
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository, TaskRepository, FileRepository


class TestApiEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "media.db")
        init_db(self.db_path)

        # Create test client in DB
        conn = get_db_connection(self.db_path)
        self.client_repo = ClientRepository(conn)
        self.api_key = "test_key_123"
        key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()
        self.client = self.client_repo.create_client("T", key_hash, 100000, concurrency_limit=2)
        conn.close()

        self.app = FastAPI()

        # Override DB dependency to use the temporary test DB
        def override_get_db():
            conn = get_db_connection(self.db_path)
            try:
                yield conn
            finally:
                conn.close()

        self.app.dependency_overrides[get_db] = override_get_db

        self.app.include_router(extract_router, prefix="/v1")
        self.app.include_router(tasks_router, prefix="/v1")
        self.app.include_router(files_router, prefix="/v1")
        self.app.include_router(admin_router, prefix="/v1")
        self.client_http = TestClient(self.app)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_endpoint_unauthorized(self):
        res = self.client_http.post("/v1/extract", json={"url": "https://soundcloud.com/test"})
        self.assertEqual(res.status_code, 401)

    def test_admin_create_client(self):
        res = self.client_http.post(
            "/v1/admin/clients",
            headers={"X-Admin-Key": "admin-secret-key"},
            json={"name": "New Client", "daily_bytes_quota": 5000000},
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIn("api_key", data)
        self.assertEqual(data["name"], "New Client")

    def test_task_submission_and_status(self):
        # Submit valid task
        res = self.client_http.post(
            "/v1/tasks",
            headers={"X-API-Key": self.api_key},
            json={"url": "https://soundcloud.com/artist/track"},
        )
        self.assertEqual(res.status_code, 202)
        task_data = res.json()
        task_id = task_data["id"]
        self.assertEqual(task_data["status"], "queued")

        # Query task status
        res_status = self.client_http.get(
            f"/v1/tasks/{task_id}",
            headers={"X-API-Key": self.api_key},
        )
        self.assertEqual(res_status.status_code, 200)
        self.assertEqual(res_status.json()["status"], "queued")

        # Cancel task
        res_cancel = self.client_http.delete(
            f"/v1/tasks/{task_id}",
            headers={"X-API-Key": self.api_key},
        )
        self.assertEqual(res_cancel.status_code, 200)
        self.assertEqual(res_cancel.json()["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()

