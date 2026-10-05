import unittest
import hashlib
import tempfile
import os
from fastapi import HTTPException
from app.api.deps import verify_client_key, verify_admin_key
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository


class TestApiDeps(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "media.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)
        self.client_repo = ClientRepository(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_verify_client_key(self):
        raw_key = "secret_client_key"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        client = self.client_repo.create_client("Test", key_hash, 1000)

        verified = verify_client_key(raw_key, self.client_repo)
        self.assertEqual(verified["id"], client["id"])

        with self.assertRaises(HTTPException):
            verify_client_key("wrong_key", self.client_repo)

        with self.assertRaises(HTTPException):
            verify_client_key(None, self.client_repo)

    def test_verify_admin_key(self):
        self.assertTrue(verify_admin_key("test-admin", "test-admin"))
        with self.assertRaises(HTTPException):
            verify_admin_key("wrong-admin", "test-admin")
        with self.assertRaises(HTTPException):
            verify_admin_key(None, "test-admin")


if __name__ == "__main__":
    unittest.main()

