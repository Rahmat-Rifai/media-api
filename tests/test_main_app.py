import unittest
from starlette.testclient import TestClient
from app.main import app


class TestMainApp(unittest.TestCase):
    def test_health_check(self):
        with TestClient(app) as client:
            res = client.get("/health")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()

