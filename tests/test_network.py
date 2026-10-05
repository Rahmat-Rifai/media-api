import unittest
from unittest.mock import patch, MagicMock
from app.core.network import CurlRH, CurlAdapter, setup_network_profile
from yt_dlp.networking.exceptions import TransportError, HTTPError
import requests
import gallery_dl.extractor.common

class TestNetwork(unittest.TestCase):
    def test_curl_rh_fail_fast_on_post(self):
        rh = CurlRH()
        mock_req = MagicMock()
        mock_req.method = "POST"
        mock_req.url = "https://api.example.com/data"
        with self.assertRaises(TransportError) as ctx:
            rh._send(mock_req)
        self.assertIn("Egress POST blocked by policy", str(ctx.exception))

    def test_curl_rh_fail_fast_on_other_methods(self):
        rh = CurlRH()
        for method in ("PUT", "DELETE", "PATCH"):
            mock_req = MagicMock()
            mock_req.method = method
            mock_req.url = "https://api.example.com/data"
            with self.assertRaises(TransportError):
                rh._send(mock_req)

    @patch("subprocess.run")
    def test_curl_rh_get_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nHello World",
            stderr=b"",
        )
        rh = CurlRH()
        mock_req = MagicMock()
        mock_req.method = "GET"
        mock_req.url = "https://api.example.com/resource"
        mock_req.headers = {"Accept": "text/plain"}
        res = rh._send(mock_req)

        self.assertEqual(res.status, 200)
        self.assertEqual(res.url, "https://api.example.com/resource")
        self.assertEqual(res.headers.get("Content-Type"), "text/plain")
        self.assertEqual(res.read(), b"Hello World")

        cmd = mock_run.call_args[0][0]
        self.assertIn("/usr/bin/curl", cmd)
        self.assertIn("--connect-timeout", cmd)
        self.assertIn("10", cmd)
        self.assertIn("--max-time", cmd)
        self.assertIn("30", cmd)
        self.assertIn("-H", cmd)
        self.assertIn("Connection: close", cmd)

    @patch("subprocess.run")
    def test_curl_rh_head_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n",
            stderr=b"",
        )
        rh = CurlRH()
        mock_req = MagicMock()
        mock_req.method = "HEAD"
        mock_req.url = "https://api.example.com/resource"
        mock_req.headers = {}
        res = rh._send(mock_req)
        self.assertEqual(res.status, 200)

        cmd = mock_run.call_args[0][0]
        self.assertIn("-I", cmd)

    @patch("subprocess.run")
    def test_curl_rh_http_error(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"HTTP/1.1 404 Not Found\r\n\r\nNot Found",
            stderr=b"",
        )
        rh = CurlRH()
        mock_req = MagicMock()
        mock_req.method = "GET"
        mock_req.url = "https://api.example.com/notfound"
        mock_req.headers = {}
        with self.assertRaises(HTTPError):
            rh._send(mock_req)

    @patch("subprocess.run")
    def test_curl_rh_unparseable_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=b"curl: (6) Could not resolve host",
            stderr=b"",
        )
        rh = CurlRH()
        mock_req = MagicMock()
        mock_req.method = "GET"
        mock_req.url = "https://api.example.com/bad"
        mock_req.headers = {}
        with self.assertRaises(TransportError):
            rh._send(mock_req)

    def test_curl_adapter_fail_fast_on_post(self):
        adapter = CurlAdapter()
        mock_req = MagicMock()
        mock_req.method = "POST"
        mock_req.url = "https://api.example.com/data"
        with self.assertRaises(requests.exceptions.RequestException) as ctx:
            adapter.send(mock_req)
        self.assertIn("Egress POST blocked by policy", str(ctx.exception))

    def test_curl_adapter_fail_fast_on_other_methods(self):
        adapter = CurlAdapter()
        for method in ("PUT", "DELETE", "PATCH"):
            mock_req = MagicMock()
            mock_req.method = method
            mock_req.url = "https://api.example.com/data"
            with self.assertRaises(requests.exceptions.RequestException):
                adapter.send(mock_req)

    @patch("subprocess.run")
    def test_curl_adapter_get_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"key\":\"value\"}",
            stderr=b"",
        )
        adapter = CurlAdapter()
        mock_req = MagicMock()
        mock_req.method = "GET"
        mock_req.url = "https://api.example.com/data.json"
        mock_req.headers = {}
        resp = adapter.send(mock_req)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.url, "https://api.example.com/data.json")
        self.assertEqual(resp.content, b"{\"key\":\"value\"}")
        self.assertEqual(resp.headers.get("Content-Type"), "application/json")

        cmd = mock_run.call_args[0][0]
        self.assertIn("/usr/bin/curl", cmd)
        self.assertIn("--connect-timeout", cmd)
        self.assertIn("10", cmd)
        self.assertIn("--max-time", cmd)
        self.assertIn("30", cmd)
        self.assertIn("-H", cmd)
        self.assertIn("Connection: close", cmd)

    @patch("subprocess.run")
    def test_curl_adapter_unparseable_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout=b"",
            stderr=b"Failed to connect",
        )
        adapter = CurlAdapter()
        mock_req = MagicMock()
        mock_req.method = "GET"
        mock_req.url = "https://api.example.com/broken"
        mock_req.headers = {}
        with self.assertRaises(requests.exceptions.ConnectionError):
            adapter.send(mock_req)

    @patch("yt_dlp.networking.common.register_rh")
    def test_setup_network_profile_sandbox(self, mock_reg):
        setup_network_profile("sandbox")
        mock_reg.assert_called_with(CurlRH)
        adapter = gallery_dl.extractor.common._build_requests_adapter()
        self.assertIsInstance(adapter, CurlAdapter)

    @patch("yt_dlp.networking.common.register_rh")
    def test_setup_network_profile_standard(self, mock_reg):
        setup_network_profile("standard")
        mock_reg.assert_not_called()

if __name__ == "__main__":
    unittest.main()
