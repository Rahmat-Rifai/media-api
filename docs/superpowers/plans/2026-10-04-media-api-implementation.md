# Media API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, deploy, and verify a persistent, hardened media extraction and download service (FastAPI, yt-dlp, gallery-dl, ffmpeg, SQLite WAL) running as user `mediaapi` under strict sandbox egress constraints with automated 20 GB watermark eviction.

**Architecture:** A modular Python service using FastAPI for the HTTP layer, an asynchronous worker queue (`asyncio.Semaphore(3)`) backed by SQLite (WAL mode) as the source of truth, an isolated storage engine on `/home/hatch` with atomic moves and LRU watermark cleaner, and an abstracted network layer (`NETWORK_PROFILE=sandbox|standard`) that delegates outbound traffic to `/usr/bin/curl` with fail-fast non-GET rejection.

**Tech Stack:** Python 3.12, FastAPI, Starlette, Pydantic v2, SQLite3 (WAL), yt-dlp, gallery-dl, ffmpeg 8.1.2, systemd.

**Spec:** `/home/mediaapi/media-api/docs/superpowers/specs/2026-10-04-media-api-design.md`

## Global Constraints
- Target platform: Meta Muse Linux VM (Debian-based, non-root user `mediaapi` uid=1000).
- Python executable: `/home/mediaapi/media-api/venv/bin/python3`.
- Test runner: `/home/mediaapi/media-api/venv/bin/python3 -m unittest`.
- Max storage: 20 GB hard cap on persistent volume `/home/hatch`.
- Network profile: `NETWORK_PROFILE=sandbox` (fail-fast on non-GET/HEAD, `/usr/bin/curl` subprocess with `Connection: close`).
- Platform scope (v1): Tested GET-only platforms only (`soundcloud.com`, `commons.wikimedia.org`). Direct links and outbound POST platforms (YouTube, Twitter) are rejected.
- Pure SSRF validator: Zero network/DNS I/O, scheme `https:`, port `443`, IP literals rejected.
- Standard error envelope: `{"error": {"code": str, "message": str, "retryable": bool}}`.

---

## File Structure Map

```
/home/hatch/media-api/
????????? .env.example                     # Environment template
????????? app/
???   ????????? __init__.py
???   ????????? main.py                      # FastAPI lifespan, routers, exception handlers
???   ????????? core/
???   ???   ????????? __init__.py
???   ???   ????????? config.py                # Pydantic Settings & environment constants
???   ???   ????????? errors.py                # Standard error envelope & AppException
???   ???   ????????? network.py               # CurlRH, CurlAdapter, network profile hook
???   ???   ????????? validator.py             # Pure Zero-I/O SSRF & single-item URL validator
???   ???   ????????? storage.py               # Storage paths, atomic rename, reservation
???   ???   ????????? cleaner.py               # 20 GB watermark cleaner (90% -> 70%), LRU eviction
???   ???   ????????? tokens.py                # Stateless HMAC-SHA256 download token generator
???   ???   ????????? worker.py                # Semaphore(3) queue, watchdog killpg, progress
???   ???   ????????? engines/
???   ???       ????????? __init__.py
???   ???       ????????? base.py              # Engine interface & result dataclasses
???   ???       ????????? ytdlp.py             # yt-dlp wrapper with playlist rejection
???   ???       ????????? gallerydl.py         # gallery-dl wrapper with single-post regex
???   ????????? db/
???   ???   ????????? __init__.py
???   ???   ????????? database.py              # SQLite connection, WAL mode, schema migration
???   ???   ????????? repositories.py          # Clients, tasks, files queries & state updates
???   ????????? api/
???       ????????? __init__.py
???       ????????? deps.py                  # API Key auth & active concurrency limit
???       ????????? endpoints/
???           ????????? __init__.py
???           ????????? extract.py           # POST /v1/extract
???           ????????? tasks.py             # POST /v1/tasks, GET /v1/tasks/{id}, DELETE /v1/tasks/{id}
???           ????????? files.py             # GET /v1/files/{id} (Range streaming)
???           ????????? admin.py             # POST /v1/admin/clients, GET /v1/admin/storage, POST /v1/admin/cleaner/run
????????? data/                            # Persistent SQLite database directory
???   ????????? media.db
????????? storage/                         # Persistent media storage
???   ????????? tmp/                         # Task scratch directories
???   ????????? files/                       # Final immutable media files
????????? deploy/
???   ????????? media-api.service            # Hardened systemd unit file
???   ????????? setup_env.sh                 # VM directory & permission bootstrapper
????????? tests/
    ????????? __init__.py
    ????????? test_config_and_errors.py
    ????????? test_validator.py
    ????????? test_network.py
    ????????? test_database_and_repos.py
    ????????? test_storage_and_cleaner.py
    ????????? test_tokens.py
    ????????? test_engines.py
    ????????? test_worker.py
    ????????? test_api_deps.py
    ????????? test_api_endpoints.py
    ????????? test_main_app.py
    ????????? test_platform_matrix.py
```

---

### Task 1: Core Configuration, Error Models & Standard Envelope

**Files:**
- Create: `app/__init__.py`
- Create: `app/core/__init__.py`
- Create: `app/core/config.py`
- Create: `app/core/errors.py`
- Test: `tests/test_config_and_errors.py`

**Interfaces:**
- Consumes: Standard library, Pydantic v2.
- Produces:
  - `Settings`: runtime configuration singleton `settings`.
  - `ErrorCode`: Enum of standard error strings.
  - `ErrorEnvelope`: Pydantic model `{"error": {"code": str, "message": str, "retryable": bool}}`.
  - `AppException`: Exception carrying status_code, code, message, and retryable flag.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_and_errors.py
import unittest
from app.core.config import Settings
from app.core.errors import ErrorCode, ErrorEnvelope, AppException

class TestConfigAndErrors(unittest.TestCase):
    def test_default_settings(self):
        settings = Settings(ADMIN_API_KEY="test-admin-key")
        self.assertEqual(settings.NETWORK_PROFILE, "sandbox")
        self.assertEqual(settings.STORAGE_MAX_BYTES, 20 * 1024 * 1024 * 1024)
        self.assertEqual(settings.HIGH_WATERMARK_RATIO, 0.90)
        self.assertEqual(settings.LOW_WATERMARK_RATIO, 0.70)
        self.assertEqual(settings.WORKER_CONCURRENCY, 3)

    def test_error_envelope_serialization(self):
        envelope = ErrorEnvelope.create(
            code=ErrorCode.UNSUPPORTED_PLATFORM,
            message="Domain not allowlisted",
            retryable=False
        )
        data = envelope.model_dump()
        self.assertEqual(data, {
            "error": {
                "code": "unsupported_platform",
                "message": "Domain not allowlisted",
                "retryable": False
            }
        })

    def test_app_exception(self):
        exc = AppException(
            status_code=400,
            code=ErrorCode.SSRF_DETECTED,
            message="IP literal rejected",
            retryable=False
        )
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.code, ErrorCode.SSRF_DETECTED)
        self.assertFalse(exc.retryable)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_config_and_errors.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/__init__.py
# (empty)

# app/core/__init__.py
# (empty)

# app/core/config.py
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    NETWORK_PROFILE: str = Field(default="sandbox")
    DATA_PATH: str = Field(default="data")
    STORAGE_PATH: str = Field(default="storage")
    STORAGE_MAX_BYTES: int = Field(default=20 * 1024 * 1024 * 1024)
    HIGH_WATERMARK_RATIO: float = Field(default=0.90)
    LOW_WATERMARK_RATIO: float = Field(default=0.70)
    GRACE_PERIOD_SECONDS: int = Field(default=600)
    FILE_DEFAULT_TTL_SECONDS: int = Field(default=86400)
    WORKER_CONCURRENCY: int = Field(default=3)
    EXTRACT_CONCURRENCY: int = Field(default=2)
    TASK_MAX_DURATION_SECONDS: int = Field(default=900)
    TASK_STALL_TIMEOUT_SECONDS: int = Field(default=60)
    TOKEN_SECRET: str = Field(default="change-this-in-production-hmac-secret")
    TOKEN_TTL_SECONDS: int = Field(default=900)
    ADMIN_API_KEY: str = Field(default="admin-secret-key")
    ALLOWLIST_DOMAINS: List[str] = Field(default=[
        "soundcloud.com",
        "commons.wikimedia.org",
        "upload.wikimedia.org"
    ])

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# app/core/errors.py
from enum import Enum
from pydantic import BaseModel

class ErrorCode(str, Enum):
    SSRF_DETECTED = "ssrf_detected"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    UNSUPPORTED_PLAYLIST = "unsupported_playlist"
    UNSUPPORTED_STREAM = "unsupported_stream"
    UPSTREAM_BLOCKED = "upstream_blocked"
    EGRESS_POST_BLOCKED = "egress_post_blocked"
    CONCURRENCY_LIMIT_EXCEEDED = "concurrency_limit_exceeded"
    QUEUE_FULL = "queue_full"
    DAILY_QUOTA_EXCEEDED = "daily_quota_exceeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    TASK_TIMEOUT = "task_timeout"
    TASK_STALLED = "task_stalled"
    TASK_CANCELLED = "task_cancelled"
    FILE_EXPIRED = "file_expired"
    TOKEN_INVALID = "token_invalid"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    INTERNAL_ERROR = "internal_error"

class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool

class ErrorEnvelope(BaseModel):
    error: ErrorDetail

    @classmethod
    def create(cls, code: ErrorCode | str, message: str, retryable: bool = False) -> "ErrorEnvelope":
        code_str = code.value if isinstance(code, ErrorCode) else str(code)
        return cls(error=ErrorDetail(code=code_str, message=message, retryable=retryable))

class AppException(Exception):
    def __init__(self, status_code: int, code: ErrorCode | str, message: str, retryable: bool = False):
        self.status_code = status_code
        self.code = code.value if isinstance(code, ErrorCode) else str(code)
        self.message = message
        self.retryable = retryable
        super().__init__(self.message)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_config_and_errors.py`
Expected: Ran 3 tests in 0.05s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/ tests/test_config_and_errors.py
git commit -m "feat(core): add settings and standardized error envelope"
```

---

### Task 2: Pure Zero-I/O SSRF & Input Validator

**Files:**
- Create: `app/core/validator.py`
- Test: `tests/test_validator.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, `app.core.errors.AppException`, `app.core.errors.ErrorCode`.
- Produces:
  - `validate_and_sanitize_url(url: str) -> str`: Validates HTTPS, port 443, rejects IP literals/userinfo, checks allowlist, rejects pure playlists, strips tracking params.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_validator.py
import unittest
from app.core.validator import validate_and_sanitize_url
from app.core.errors import AppException, ErrorCode

class TestValidator(unittest.TestCase):
    def test_valid_urls(self):
        url = "https://soundcloud.com/octobersveryown/drake-back-to-back-freestyle?si=123&utm_source=copy"
        sanitized = validate_and_sanitize_url(url)
        self.assertEqual(sanitized, "https://soundcloud.com/octobersveryown/drake-back-to-back-freestyle")

        wiki_url = "https://commons.wikimedia.org/wiki/File:Monarch_In_May.jpg"
        self.assertEqual(validate_and_sanitize_url(wiki_url), wiki_url)

    def test_reject_scheme(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("http://soundcloud.com/track")
        self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_reject_ip_literals(self):
        ips = [
            "https://127.0.0.1/test",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/test",
            "https://[::1]/test",
            "https://0x7f000001/test"
        ]
        for ip in ips:
            with self.assertRaises(AppException) as ctx:
                validate_and_sanitize_url(ip)
            self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_reject_userinfo_and_ports(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://user:pass@soundcloud.com/track")
        self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://soundcloud.com:8443/track")
        self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_reject_unallowlisted_domain(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://evil.com/video")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLATFORM)

    def test_reject_pure_playlist(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://soundcloud.com/artist/sets/my-playlist")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_validator.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.validator'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/validator.py
import re
import urllib.parse
from app.core.config import settings
from app.core.errors import AppException, ErrorCode

IPV4_PATTERN = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
HEX_OR_INT_IP_PATTERN = re.compile(r"^(0x[0-9a-fA-F]+|\d+)$")
DISALLOWED_PATH_PATTERNS = [
    re.compile(r"/sets/"),
    re.compile(r"/channel/"),
    re.compile(r"/playlist"),
    re.compile(r"/albums?/")
]
TRACKING_PARAMS = {"si", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref", "fbclid"}

def is_ip_literal(host: str) -> bool:
    if host.startswith("[") and host.endswith("]"):
        return True
    if IPV4_PATTERN.match(host) or HEX_OR_INT_IP_PATTERN.match(host):
        return True
    return False

def is_domain_allowed(host: str) -> bool:
    host = host.lower()
    for allowed in settings.ALLOWLIST_DOMAINS:
        if host == allowed or host.endswith("." + allowed):
            return True
    return False

def validate_and_sanitize_url(url: str) -> str:
    if not url or not isinstance(url, str):
        raise AppException(400, ErrorCode.SSRF_DETECTED, "URL must be a non-empty string", retryable=False)

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        raise AppException(400, ErrorCode.SSRF_DETECTED, "Malformed URL format", retryable=False)

    if parsed.scheme.lower() != "https":
        raise AppException(400, ErrorCode.SSRF_DETECTED, "Only HTTPS URLs are allowed", retryable=False)

    if parsed.username or parsed.password:
        raise AppException(400, ErrorCode.SSRF_DETECTED, "Userinfo in URL is forbidden", retryable=False)

    if parsed.port not in (None, 443):
        raise AppException(400, ErrorCode.SSRF_DETECTED, "Non-standard ports are forbidden", retryable=False)

    host = parsed.hostname or ""
    if not host or is_ip_literal(host):
        raise AppException(400, ErrorCode.SSRF_DETECTED, "IP literals and empty hosts are forbidden", retryable=False)

    if not is_domain_allowed(host):
        raise AppException(400, ErrorCode.UNSUPPORTED_PLATFORM, f"Domain '{host}' is not in the verified allowlist", retryable=False)

    for pattern in DISALLOWED_PATH_PATTERNS:
        if pattern.search(parsed.path):
            raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST, "Playlists, sets, and channels are not supported", retryable=False)

    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    cleaned_params = [(k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS]
    new_query = urllib.parse.urlencode(cleaned_params)

    sanitized = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, new_query, ""))
    return sanitized
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_validator.py`
Expected: Ran 6 tests in 0.05s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/core/validator.py tests/test_validator.py
git commit -m "feat(core): implement zero-I/O SSRF and single-item URL validator"
```

---

### Task 3: Network Profile Abstraction (`CurlRH`, `CurlAdapter`, Fail-Fast POST)

**Files:**
- Create: `app/core/network.py`
- Test: `tests/test_network.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, `yt-dlp`, `gallery-dl`, `requests`.
- Produces:
  - `CurlRH`: Custom `yt_dlp` request handler failing fast on non-GET/HEAD.
  - `CurlAdapter`: Custom `requests.adapters.HTTPAdapter` failing fast on non-GET/HEAD.
  - `setup_network_profile()`: Configures network handlers according to `settings.NETWORK_PROFILE`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_network.py
import unittest
from unittest.mock import patch, MagicMock
from app.core.network import CurlRH, CurlAdapter, setup_network_profile
from yt_dlp.networking.exceptions import TransportError
import requests

class TestNetwork(unittest.TestCase):
    def test_curl_rh_fail_fast_on_post(self):
        rh = CurlRH()
        mock_req = MagicMock()
        mock_req.method = "POST"
        mock_req.url = "https://api.example.com/data"
        with self.assertRaises(TransportError) as ctx:
            rh._send(mock_req)
        self.assertIn("Egress POST blocked by policy", str(ctx.exception))

    def test_curl_adapter_fail_fast_on_post(self):
        adapter = CurlAdapter()
        mock_req = MagicMock()
        mock_req.method = "POST"
        mock_req.url = "https://api.example.com/data"
        with self.assertRaises(requests.exceptions.RequestException) as ctx:
            adapter.send(mock_req)
        self.assertIn("Egress POST blocked by policy", str(ctx.exception))

    @patch("yt_dlp.networking.common.register_rh")
    def test_setup_network_profile_sandbox(self, mock_reg):
        setup_network_profile("sandbox")
        mock_reg.assert_called_with(CurlRH)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_network.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.network'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/network.py
import io
import subprocess
import requests
from requests.adapters import HTTPAdapter
from requests.models import Response as RequestsResponse
from urllib3.response import HTTPResponse

import yt_dlp
from yt_dlp.networking.common import RequestHandler, Response as YtDlpResponse, register_rh, Features
from yt_dlp.networking.exceptions import HTTPError, TransportError

class CurlRH(RequestHandler):
    RH_KEY = "Curl"
    RH_NAME = "curl"
    _SUPPORTED_URL_SCHEMES = ("http", "https")
    _SUPPORTED_PROXY_SCHEMES = ("http", "https")
    _SUPPORTED_FEATURES = (Features.ALL_PROXY, Features.NO_PROXY)

    def _send(self, request):
        method = (request.method or "GET").upper()
        if method not in ("GET", "HEAD"):
            raise TransportError(f"Egress POST blocked by policy: {method}")

        cmd = ["/usr/bin/curl", "-s", "-i", "-L", "--connect-timeout", "10", "--max-time", "30"]
        if method == "HEAD":
            cmd.append("-I")

        headers = dict(request.headers) if request.headers else {}
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        for k, v in headers.items():
            if k.lower() not in ("connection", "accept-encoding", "content-length"):
                cmd.extend(["-H", f"{k}: {v}"])
        cmd.extend(["-H", "Connection: close"])
        cmd.append(request.url)

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
        out = proc.stdout
        blocks = out.split(b"\r\n\r\n")
        if len(blocks) < 2:
            blocks = out.split(b"\n\n")

        header_block = None
        body_idx = 0
        for idx, block in enumerate(blocks[:-1]):
            if block.startswith(b"HTTP/"):
                header_block = block
                body_idx = idx + 1

        if not header_block:
            raise TransportError(f"Failed to parse curl response from {request.url}")

        body = b"\r\n\r\n".join(blocks[body_idx:])
        lines = header_block.split(b"\r\n")
        status = int(lines[0].decode("latin1").split()[1])

        res_headers = {}
        for line in lines[1:]:
            line_str = line.decode("latin1")
            if ":" in line_str:
                k, v = line_str.split(":", 1)
                res_headers[k.strip()] = v.strip()

        res = YtDlpResponse(fp=io.BytesIO(body), headers=res_headers, url=request.url, status=status)
        if not (200 <= status < 300):
            raise HTTPError(res)
        return res

class CurlAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        method = (request.method or "GET").upper()
        if method not in ("GET", "HEAD"):
            raise requests.exceptions.RequestException(f"Egress POST blocked by policy: {method}")

        cmd = ["/usr/bin/curl", "-s", "-i", "-L", "--connect-timeout", "10", "--max-time", "30"]
        if method == "HEAD":
            cmd.append("-I")

        headers = dict(request.headers) if request.headers else {}
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        for k, v in headers.items():
            if k.lower() not in ("connection", "accept-encoding", "content-length"):
                cmd.extend(["-H", f"{k}: {v}"])
        cmd.extend(["-H", "Connection: close"])
        cmd.append(request.url)

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
        out = proc.stdout
        blocks = out.split(b"\r\n\r\n")
        if len(blocks) < 2:
            blocks = out.split(b"\n\n")

        header_block = None
        body_idx = 0
        for idx, block in enumerate(blocks[:-1]):
            if block.startswith(b"HTTP/"):
                header_block = block
                body_idx = idx + 1

        if not header_block:
            raise requests.exceptions.ConnectionError("Failed to parse curl response")

        body = b"\r\n\r\n".join(blocks[body_idx:])
        lines = header_block.split(b"\r\n")
        status = int(lines[0].decode("latin1").split()[1])

        raw_res = HTTPResponse(body=io.BytesIO(body), status=status, preload_content=False)
        resp = RequestsResponse()
        resp.status_code = status
        resp.raw = raw_res
        resp.url = request.url
        resp.request = request
        return resp

def setup_network_profile(profile: str = "sandbox"):
    if profile == "sandbox":
        register_rh(CurlRH)
        import gallery_dl.extractor.common
        gallery_dl.extractor.common._build_requests_adapter = lambda *a, **k: CurlAdapter()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_network.py`
Expected: Ran 3 tests in 0.08s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/core/network.py tests/test_network.py
git commit -m "feat(core): implement network profile abstraction and fail-fast non-GET"
```

---

### Task 4: Database Setup, Schema & Repositories

**Files:**
- Create: `app/db/__init__.py`
- Create: `app/db/database.py`
- Create: `app/db/repositories.py`
- Test: `tests/test_database_and_repos.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, standard `sqlite3`.
- Produces:
  - `init_db(db_path: str = None)`: Executes schema creation and sets WAL mode + pragmas.
  - `get_db_connection(db_path: str = None) -> sqlite3.Connection`.
  - `ClientRepository`: Methods `create_client`, `get_by_api_key_hash`, `get_by_id`, `increment_daily_usage`.
  - `TaskRepository`: Methods `create_task`, `get_task`, `update_status`, `update_progress`, `reset_running_tasks_on_startup`.
  - `FileRepository`: Methods `create_file`, `get_file`, `update_last_accessed`, `list_expired_or_evictable`, `delete_file`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database_and_repos.py
import unittest
import os
import tempfile
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository, TaskRepository, FileRepository

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

        client_repo.increment_daily_usage(client["id"], 500)
        updated = client_repo.get_by_id(client["id"])
        self.assertEqual(updated["daily_bytes_used"], 500)
        conn.close()

    def test_task_lifecycle_and_startup_reset(self):
        conn = get_db_connection(self.db_path)
        client_repo = ClientRepository(conn)
        task_repo = TaskRepository(conn)
        client = client_repo.create_client(name="c", api_key_hash="h", daily_bytes_quota=1000)

        task = task_repo.create_task(client["id"], "https://soundcloud.com/track")
        self.assertEqual(task["status"], "queued")

        task_repo.update_status(task["id"], "running")
        running_task = task_repo.get_task(task["id"])
        self.assertEqual(running_task["status"], "running")

        # Simulate restart recovery
        reset_count = task_repo.reset_running_tasks_on_startup()
        self.assertEqual(reset_count, 1)
        recovered = task_repo.get_task(task["id"])
        self.assertEqual(recovered["status"], "failed")
        self.assertEqual(recovered["error_code"], "system_restart")
        conn.close()

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_database_and_repos.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/db/__init__.py
# (empty)

# app/db/database.py
import sqlite3
import os
from app.core.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    daily_bytes_quota INTEGER NOT NULL,
    daily_bytes_used INTEGER NOT NULL DEFAULT 0,
    concurrency_limit INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    format_id TEXT,
    max_height INTEGER,
    audio_only INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0.0,
    estimated_bytes INTEGER,
    reserved_bytes INTEGER NOT NULL DEFAULT 0,
    actual_bytes INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL REFERENCES clients(id),
    filename TEXT NOT NULL,
    original_title TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_client_status ON tasks(client_id, status);
CREATE INDEX IF NOT EXISTS idx_files_client_id ON files(client_id);
CREATE INDEX IF NOT EXISTS idx_files_expires_at ON files(expires_at);
CREATE INDEX IF NOT EXISTS idx_files_last_accessed ON files(last_accessed_at);
"""

def get_db_path(custom_path: str = None) -> str:
    if custom_path:
        return custom_path
    os.makedirs(settings.DATA_PATH, exist_ok=True)
    return os.path.join(settings.DATA_PATH, "media.db")

def get_db_connection(custom_path: str = None) -> sqlite3.Connection:
    path = get_db_path(custom_path)
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_db(custom_path: str = None):
    conn = get_db_connection(custom_path)
    with conn:
        conn.executescript(SCHEMA)
    conn.close()

# app/db/repositories.py
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

    def create_task(self, client_id: str, url: str, format_id: str = None, max_height: int = None, audio_only: bool = False) -> Dict[str, Any]:
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

    def update_status(self, task_id: str, status: str, error_code: str = None, error_message: str = None):
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
               WHERE (strftime('%s', 'now') - strftime('%s', last_accessed_at)) > ?
                  OR strftime('%s', expires_at) <= strftime('%s', 'now')
               ORDER BY last_accessed_at ASC""",
            (grace_period_seconds,)
        )
        return [dict(r) for r in cur.fetchall()]

    def delete_file(self, file_id: str):
        with self.conn:
            self.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_database_and_repos.py`
Expected: Ran 2 tests in 0.08s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/db/ tests/test_database_and_repos.py
git commit -m "feat(db): add sqlite database setup, schema, and repositories"
```

---

### Task 5: Storage Engine, Disk Reservation & Watermark Cleaner

**Files:**
- Create: `app/core/storage.py`
- Create: `app/core/cleaner.py`
- Test: `tests/test_storage_and_cleaner.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, `app.db.repositories.FileRepository`.
- Produces:
  - `StorageManager`: Methods `get_tmp_dir`, `get_file_path`, `commit_file_atomically`, `get_dir_size`, `reserve_disk_space`.
  - `CleanerService`: Method `run_eviction_cycle(force_emergency: bool = False) -> Dict[str, Any]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage_and_cleaner.py
import unittest
import os
import tempfile
from app.core.storage import StorageManager
from app.core.cleaner import CleanerService
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

        final_path = self.storage_mgr.commit_file_atomically(task_id, "source.mp3", "fl_final123.mp3")
        self.assertTrue(os.path.exists(final_path))
        self.assertFalse(os.path.exists(test_file))

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

        f_record = file_repo.create_file(
            task_id=task["id"],
            client_id=client["id"],
            filename="fl_evict.mp3",
            original_title="Evict Me",
            mime_type="audio/mpeg",
            size_bytes=1024,
            storage_path=file_path,
            expires_at="2000-01-01 00:00:00"
        )

        cleaner = CleanerService(file_repo=file_repo, storage_mgr=self.storage_mgr, max_bytes=2048, low_watermark=1024)
        stats = cleaner.run_eviction_cycle()
        self.assertEqual(stats["evicted_count"], 1)
        self.assertFalse(os.path.exists(file_path))
        conn.close()

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_storage_and_cleaner.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.storage'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/storage.py
import os
import shutil
from typing import Optional
from app.core.config import settings
from app.core.errors import AppException, ErrorCode

class StorageManager:
    def __init__(self, base_path: str = None):
        self.base_path = base_path or settings.STORAGE_PATH
        self.tmp_base = os.path.join(self.base_path, "tmp")
        self.files_base = os.path.join(self.base_path, "files")
        os.makedirs(self.tmp_base, exist_ok=True)
        os.makedirs(self.files_base, exist_ok=True)

    def get_tmp_dir(self, task_id: str) -> str:
        d = os.path.join(self.tmp_base, task_id)
        os.makedirs(d, exist_ok=True)
        return d

    def cleanup_tmp_dir(self, task_id: str):
        d = os.path.join(self.tmp_base, task_id)
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    def get_files_dir(self) -> str:
        return self.files_base

    def get_file_path(self, filename: str) -> str:
        return os.path.join(self.files_base, filename)

    def commit_file_atomically(self, task_id: str, src_filename: str, dst_filename: str) -> str:
        src = os.path.join(self.get_tmp_dir(task_id), src_filename)
        dst = os.path.join(self.files_base, dst_filename)
        if not os.path.exists(src):
            raise AppException(500, ErrorCode.INTERNAL_ERROR, f"Source file does not exist: {src}")
        os.replace(src, dst)
        return dst

    def get_dir_size(self, path: str) -> int:
        total = 0
        if not os.path.exists(path):
            return 0
        for root, dirs, files in os.walk(path):
            for f in files:
                total += os.path.getsize(os.path.join(root, f))
        return total

# app/core/cleaner.py
import os
import logging
from typing import Dict, Any
from app.core.config import settings
from app.core.storage import StorageManager
from app.db.repositories import FileRepository

logger = logging.getLogger(__name__)

class CleanerService:
    def __init__(self, file_repo: FileRepository, storage_mgr: StorageManager, max_bytes: int = None, low_watermark: int = None):
        self.file_repo = file_repo
        self.storage_mgr = storage_mgr
        self.max_bytes = max_bytes or settings.STORAGE_MAX_BYTES
        self.high_watermark = int(self.max_bytes * settings.HIGH_WATERMARK_RATIO)
        self.low_watermark = low_watermark or int(self.max_bytes * settings.LOW_WATERMARK_RATIO)
        self.grace_period = settings.GRACE_PERIOD_SECONDS

    def run_eviction_cycle(self, force_emergency: bool = False) -> Dict[str, Any]:
        total_used = self.file_repo.get_total_storage_used()
        logger.info(f"Cleaner check: used {total_used} / max {self.max_bytes}")

        evicted_count = 0
        freed_bytes = 0

        if total_used >= self.high_watermark or force_emergency:
            candidates = self.file_repo.list_evictable_files(self.grace_period)
            for f in candidates:
                if total_used <= self.low_watermark and not force_emergency:
                    break
                path = f["storage_path"]
                if os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError as e:
                        logger.error(f"Failed to unlink {path}: {e}")
                self.file_repo.delete_file(f["id"])
                total_used -= f["size_bytes"]
                freed_bytes += f["size_bytes"]
                evicted_count += 1

        return {
            "evicted_count": evicted_count,
            "freed_bytes": freed_bytes,
            "current_storage_used": total_used
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_storage_and_cleaner.py`
Expected: Ran 2 tests in 0.07s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/core/storage.py app/core/cleaner.py tests/test_storage_and_cleaner.py
git commit -m "feat(core): implement storage manager and watermark eviction cleaner"
```

---

### Task 6: Stateless HMAC Download Tokens

**Files:**
- Create: `app/core/tokens.py`
- Test: `tests/test_tokens.py`

**Interfaces:**
- Consumes: `app.core.config.settings`.
- Produces:
  - `generate_download_token(file_id: str, expires_at_timestamp: int) -> str`.
  - `verify_download_token(token: str, file_id: str) -> bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tokens.py
import unittest
import time
from app.core.tokens import generate_download_token, verify_download_token

class TestTokens(unittest.TestCase):
    def test_token_generation_and_validation(self):
        file_id = "fl_1234567890abcdef1234567890abcdef"
        exp = int(time.time()) + 300
        token = generate_download_token(file_id, exp)

        self.assertTrue(verify_download_token(token, file_id))
        self.assertFalse(verify_download_token(token, "fl_different_id"))
        self.assertFalse(verify_download_token("invalid.token.payload", file_id))

    def test_expired_token(self):
        file_id = "fl_1234567890abcdef1234567890abcdef"
        exp = int(time.time()) - 10
        token = generate_download_token(file_id, exp)
        self.assertFalse(verify_download_token(token, file_id))

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_tokens.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.tokens'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/tokens.py
import hmac
import hashlib
import base64
import time
from app.core.config import settings

def _sign(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")

def generate_download_token(file_id: str, expires_at_timestamp: int, secret: str = None) -> str:
    secret = secret or settings.TOKEN_SECRET
    payload = f"{file_id}.{expires_at_timestamp}"
    signature = _sign(payload, secret)
    token_str = f"{payload}.{signature}"
    return base64.urlsafe_b64encode(token_str.encode("utf-8")).decode("utf-8")

def verify_download_token(token: str, file_id: str, secret: str = None) -> bool:
    secret = secret or settings.TOKEN_SECRET
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = raw.split(".")
        if len(parts) != 3:
            return False
        token_file_id, exp_str, signature = parts
        if not hmac.compare_digest(token_file_id, file_id):
            return False
        exp = int(exp_str)
        if time.time() > exp:
            return False
        expected_sig = _sign(f"{token_file_id}.{exp_str}", secret)
        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_tokens.py`
Expected: Ran 2 tests in 0.05s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/core/tokens.py tests/test_tokens.py
git commit -m "feat(core): implement stateless HMAC download token generation and verification"
```

---

### Task 7: Engine Wrappers (`yt-dlp` & `gallery-dl` with playlist prevention)

**Files:**
- Create: `app/core/engines/__init__.py`
- Create: `app/core/engines/base.py`
- Create: `app/core/engines/ytdlp.py`
- Create: `app/core/engines/gallerydl.py`
- Test: `tests/test_engines.py`

**Interfaces:**
- Consumes: `app.core.errors.AppException`, `app.core.errors.ErrorCode`, `yt_dlp`, `gallery_dl`.
- Produces:
  - `MediaEngine`: Abstract base engine.
  - `YtDlpEngine`: Methods `extract_info(url) -> Dict[str, Any]` and `download(url, outtmpl, format_id, audio_only) -> List[str]`.
  - `GalleryDlEngine`: Methods `extract_info(url) -> Dict[str, Any]` and `download(url, outdir) -> List[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_engines.py
import unittest
from unittest.mock import patch, MagicMock
from app.core.engines.ytdlp import YtDlpEngine
from app.core.engines.gallerydl import GalleryDlEngine
from app.core.errors import AppException, ErrorCode

class TestEngines(unittest.TestCase):
    @patch("yt_dlp.YoutubeDL")
    def test_ytdlp_rejects_playlist(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {"_type": "playlist", "entries": []}
        mock_ydl_cls.return_value = mock_instance

        engine = YtDlpEngine()
        with self.assertRaises(AppException) as ctx:
            engine.extract_info("https://soundcloud.com/artist/sets/album")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

    def test_gallerydl_single_item_enforcement(self):
        engine = GalleryDlEngine()
        with self.assertRaises(AppException) as ctx:
            engine.extract_info("https://commons.wikimedia.org/wiki/Category:Animals")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_engines.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.engines'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/engines/__init__.py
# (empty)

# app/core/engines/base.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List

class MediaEngine(ABC):
    @abstractmethod
    def extract_info(self, url: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    def download(self, url: str, out_dest: str, **kwargs) -> List[str]:
        pass

# app/core/engines/ytdlp.py
import os
import yt_dlp
from yt_dlp.utils import MaxDownloadsReached
from app.core.engines.base import MediaEngine
from app.core.errors import AppException, ErrorCode
from app.core.network import CurlRH
from app.core.config import settings

class YtDlpEngine(MediaEngine):
    def _get_ydl_opts(self, extra: dict = None) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "no_playlist": True,
            "playlist_items": "1",
            "max_downloads": 1,
            "allowed_extractors": ["default", "-generic"],
            "match_filter": yt_dlp.utils.match_filter_func("!is_live"),
        }
        if settings.NETWORK_PROFILE == "sandbox":
            opts["hls_prefer_native"] = True
        if extra:
            opts.update(extra)
        return opts

    def _create_ydl(self, opts: dict) -> yt_dlp.YoutubeDL:
        ydl = yt_dlp.YoutubeDL(opts)
        if settings.NETWORK_PROFILE == "sandbox":
            ydl._request_director.handlers = {"Curl": CurlRH(logger=ydl)}
            ydl._request_director.default_handler = CurlRH(logger=ydl)
        return ydl

    def extract_info(self, url: str) -> dict:
        opts = self._get_ydl_opts({"extract_flat": False})
        ydl = self._create_ydl(opts)
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise AppException(400, ErrorCode.UNSUPPORTED_PLATFORM, "Could not extract metadata")
            if info.get("_type") == "playlist":
                raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST, "Playlists and channels are not supported")

            formats = []
            for f in info.get("formats", []):
                formats.append({
                    "format_id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "quality": f.get("format_note") or f.get("qualityLabel") or str(f.get("height", "")),
                    "has_video": bool(f.get("vcodec") and f.get("vcodec") != "none"),
                    "has_audio": bool(f.get("acodec") and f.get("acodec") != "none"),
                    "filesize_approx": f.get("filesize") or f.get("filesize_approx") or 0
                })

            return {
                "title": info.get("title", "media"),
                "uploader": info.get("uploader"),
                "duration": info.get("duration"),
                "thumbnail": info.get("thumbnail"),
                "estimated_bytes": info.get("filesize") or info.get("filesize_approx") or 0,
                "formats": formats
            }
        except AppException:
            raise
        except Exception as e:
            err_str = str(e)
            if "POST blocked" in err_str:
                raise AppException(502, ErrorCode.EGRESS_POST_BLOCKED, "Upstream requires POST", retryable=False)
            if "Account authentication" in err_str or "blocked" in err_str.lower():
                raise AppException(502, ErrorCode.UPSTREAM_BLOCKED, err_str, retryable=False)
            raise AppException(400, ErrorCode.UNSUPPORTED_PLATFORM, err_str, retryable=False)

    def download(self, url: str, out_dest: str, format_id: str = None, audio_only: bool = False) -> list[str]:
        outtmpl = os.path.join(out_dest, "%(id)s.%(ext)s")
        extra_opts = {"outtmpl": outtmpl}
        if audio_only:
            extra_opts["format"] = "bestaudio/best"
            extra_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }]
        elif format_id:
            extra_opts["format"] = format_id

        ydl = self._create_ydl(self._get_ydl_opts(extra_opts))
        try:
            ydl.extract_info(url, download=True)
        except MaxDownloadsReached:
            pass
        except Exception as e:
            raise AppException(500, ErrorCode.INTERNAL_ERROR, f"Download failed: {e}")

        files = [os.path.join(out_dest, f) for f in os.listdir(out_dest) if os.path.isfile(os.path.join(out_dest, f))]
        return files

# app/core/engines/gallerydl.py
import os
import re
from app.core.engines.base import MediaEngine
from app.core.errors import AppException, ErrorCode
import gallery_dl.job
from gallery_dl import config

WIKI_SINGLE_PATTERN = re.compile(r"^https://commons\.wikimedia\.org/wiki/File:[^/]+$")

class GalleryDlEngine(MediaEngine):
    def extract_info(self, url: str) -> dict:
        if not WIKI_SINGLE_PATTERN.match(url):
            raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST, "Only single media item URLs are supported")
        return {
            "title": os.path.basename(url),
            "uploader": "Wikimedia Commons",
            "duration": None,
            "thumbnail": None,
            "estimated_bytes": 1000000,
            "formats": [{"format_id": "original", "ext": "jpg", "quality": "original", "has_video": False, "has_audio": False, "filesize_approx": 1000000}]
        }

    def download(self, url: str, out_dest: str, **kwargs) -> list[str]:
        if not WIKI_SINGLE_PATTERN.match(url):
            raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST, "Only single media item URLs are supported")
        config.set(("extractor",), "base-directory", out_dest)
        job = gallery_dl.job.DownloadJob(url)
        ret = job.run()
        if ret != 0:
            raise AppException(500, ErrorCode.INTERNAL_ERROR, f"gallery-dl job failed with code {ret}")

        downloaded = []
        for root, dirs, files in os.walk(out_dest):
            for f in files:
                downloaded.append(os.path.join(root, f))
        return downloaded
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_engines.py`
Expected: Ran 2 tests in 0.09s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/core/engines/ tests/test_engines.py
git commit -m "feat(core): implement yt-dlp and gallery-dl engine wrappers"
```

---

### Task 8: Worker Queue, Process Watchdog & Task Lifecycle State Machine

**Files:**
- Create: `app/core/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `app.core.storage.StorageManager`, `app.db.repositories.TaskRepository`, `app.db.repositories.FileRepository`, `app.core.engines.*`.
- Produces:
  - `WorkerService`: Methods `enqueue_task`, `execute_task(task_id: str)`, `cancel_task(task_id: str)`.
  - State machine transitions: `queued -> preparing -> running -> done / failed / cancelled`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py
import unittest
import os
import tempfile
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
            storage_mgr=self.storage_mgr
        )
        worker.cancel_task(task["id"])
        updated = self.task_repo.get_task(task["id"])
        self.assertEqual(updated["status"], "cancelled")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_worker.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.worker'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/worker.py
import asyncio
import os
import mimetypes
from datetime import datetime, timedelta
from typing import Dict
from app.core.config import settings
from app.core.storage import StorageManager
from app.core.errors import AppException, ErrorCode
from app.core.engines.ytdlp import YtDlpEngine
from app.core.engines.gallerydl import GalleryDlEngine
from app.db.repositories import TaskRepository, FileRepository, ClientRepository

class WorkerService:
    def __init__(self, task_repo: TaskRepository, file_repo: FileRepository, client_repo: ClientRepository, storage_mgr: StorageManager):
        self.task_repo = task_repo
        self.file_repo = file_repo
        self.client_repo = client_repo
        self.storage_mgr = storage_mgr
        self.semaphore = asyncio.Semaphore(settings.WORKER_CONCURRENCY)
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.ytdlp_engine = YtDlpEngine()
        self.gallerydl_engine = GalleryDlEngine()

    def _get_engine_for_url(self, url: str):
        if "wikimedia.org" in url:
            return self.gallerydl_engine
        return self.ytdlp_engine

    def cancel_task(self, task_id: str):
        if task_id in self.active_tasks:
            t = self.active_tasks[task_id]
            t.cancel()
        self.task_repo.update_status(task_id, "cancelled")
        self.storage_mgr.cleanup_tmp_dir(task_id)

    async def execute_task(self, task_id: str):
        async with self.semaphore:
            task = self.task_repo.get_task(task_id)
            if not task or task["status"] == "cancelled":
                return

            self.task_repo.update_status(task_id, "preparing")
            engine = self._get_engine_for_url(task["url"])

            try:
                info = engine.extract_info(task["url"])
                est_bytes = info.get("estimated_bytes") or 5 * 1024 * 1024
                reserved = int(est_bytes * 2.2)
                self.task_repo.set_reservation(task_id, est_bytes, reserved)

                tmp_dir = self.storage_mgr.get_tmp_dir(task_id)
                files = engine.download(
                    task["url"],
                    tmp_dir,
                    format_id=task.get("format_id"),
                    audio_only=bool(task.get("audio_only"))
                )

                if not files:
                    raise AppException(500, ErrorCode.INTERNAL_ERROR, "No files generated by download")

                total_actual = 0
                expires_at = (datetime.utcnow() + timedelta(seconds=settings.FILE_DEFAULT_TTL_SECONDS)).strftime("%Y-%m-%d %H:%M:%S")

                for fpath in files:
                    fname = os.path.basename(fpath)
                    fsize = os.path.getsize(fpath)
                    total_actual += fsize
                    ext = os.path.splitext(fname)[1]
                    file_id_name = f"fl_{os.urandom(16).hex()}{ext}"

                    dst_path = self.storage_mgr.commit_file_atomically(task_id, fname, file_id_name)
                    mime, _ = mimetypes.guess_type(dst_path)

                    self.file_repo.create_file(
                        task_id=task_id,
                        client_id=task["client_id"],
                        filename=file_id_name,
                        original_title=info.get("title", fname),
                        mime_type=mime or "application/octet-stream",
                        size_bytes=fsize,
                        storage_path=dst_path,
                        expires_at=expires_at
                    )

                self.client_repo.increment_daily_usage(task["client_id"], total_actual)
                self.task_repo.complete_task(task_id, total_actual)
                self.storage_mgr.cleanup_tmp_dir(task_id)

            except asyncio.CancelledError:
                self.task_repo.update_status(task_id, "cancelled")
                self.storage_mgr.cleanup_tmp_dir(task_id)
            except AppException as ae:
                self.task_repo.update_status(task_id, "failed", ae.code, ae.message)
                self.storage_mgr.cleanup_tmp_dir(task_id)
            except Exception as e:
                self.task_repo.update_status(task_id, "failed", ErrorCode.INTERNAL_ERROR.value, str(e))
                self.storage_mgr.cleanup_tmp_dir(task_id)
            finally:
                self.active_tasks.pop(task_id, None)

    def enqueue_task(self, task_id: str):
        t = asyncio.create_task(self.execute_task(task_id))
        self.active_tasks[task_id] = t
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_worker.py`
Expected: Ran 1 test in 0.08s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/core/worker.py tests/test_worker.py
git commit -m "feat(core): implement worker queue service and lifecycle state machine"
```

---

### Task 9: API Dependencies & Client / Admin Authentication

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/deps.py`
- Test: `tests/test_api_deps.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, `app.db.database`, `app.db.repositories`.
- Produces:
  - `get_current_client(x_api_key: str)`: FastAPI Header dependency verifying client API key.
  - `require_admin(x_admin_key: str)`: FastAPI Header dependency verifying admin API key.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_deps.py
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

    def test_verify_admin_key(self):
        self.assertTrue(verify_admin_key("test-admin", "test-admin"))
        with self.assertRaises(HTTPException):
            verify_admin_key("wrong-admin", "test-admin")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_api_deps.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/api/__init__.py
# (empty)

# app/api/deps.py
import hashlib
import hmac
from fastapi import Header, HTTPException, Depends
from app.core.config import settings
from app.core.errors import ErrorCode, ErrorEnvelope
from app.db.database import get_db_connection
from app.db.repositories import ClientRepository, TaskRepository, FileRepository

def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()

def get_client_repo(conn = Depends(get_db)) -> ClientRepository:
    return ClientRepository(conn)

def get_task_repo(conn = Depends(get_db)) -> TaskRepository:
    return TaskRepository(conn)

def get_file_repo(conn = Depends(get_db)) -> FileRepository:
    return FileRepository(conn)

def verify_client_key(x_api_key: str, client_repo: ClientRepository) -> dict:
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail=ErrorEnvelope.create(ErrorCode.UNAUTHORIZED, "X-API-Key header required").model_dump()
        )
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    client = client_repo.get_by_api_key_hash(key_hash)
    if not client:
        raise HTTPException(
            status_code=401,
            detail=ErrorEnvelope.create(ErrorCode.UNAUTHORIZED, "Invalid API key").model_dump()
        )
    return client

def get_current_client(x_api_key: str = Header(None), client_repo: ClientRepository = Depends(get_client_repo)) -> dict:
    return verify_client_key(x_api_key, client_repo)

def verify_admin_key(x_admin_key: str, expected_key: str = None) -> bool:
    expected = expected_key or settings.ADMIN_API_KEY
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(
            status_code=403,
            detail=ErrorEnvelope.create(ErrorCode.UNAUTHORIZED, "Invalid admin key").model_dump()
        )
    return True

def require_admin(x_admin_key: str = Header(None)) -> bool:
    return verify_admin_key(x_admin_key)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_api_deps.py`
Expected: Ran 2 tests in 0.05s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/api/ tests/test_api_deps.py
git commit -m "feat(api): implement authentication and authorization dependencies"
```

---

### Task 10: API Endpoints (`/v1/extract`, `/v1/tasks`, `/v1/files`, `/v1/admin`)

**Files:**
- Create: `app/api/endpoints/__init__.py`
- Create: `app/api/endpoints/extract.py`
- Create: `app/api/endpoints/tasks.py`
- Create: `app/api/endpoints/files.py`
- Create: `app/api/endpoints/admin.py`
- Test: `tests/test_api_endpoints.py`

**Interfaces:**
- Consumes: All `app.core.*` modules, `app.api.deps`.
- Produces:
  - `extract_router`: Mountable at `/v1/extract`.
  - `tasks_router`: Mountable at `/v1/tasks`.
  - `files_router`: Mountable at `/v1/files`.
  - `admin_router`: Mountable at `/v1/admin`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_endpoints.py
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
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository

class TestApiEndpoints(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "media.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)
        self.client_repo = ClientRepository(self.conn)

        self.api_key = "test_key_123"
        key_hash = hashlib.sha256(self.api_key.encode()).hexdigest()
        self.client = self.client_repo.create_client("T", key_hash, 100000)

        self.app = FastAPI()
        self.app.include_router(extract_router, prefix="/v1")
        self.app.include_router(tasks_router, prefix="/v1")
        self.app.include_router(files_router, prefix="/v1")
        self.app.include_router(admin_router, prefix="/v1")
        self.client_http = TestClient(self.app)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_extract_endpoint_unauthorized(self):
        res = self.client_http.post("/v1/extract", json={"url": "https://soundcloud.com/test"})
        self.assertEqual(res.status_code, 401)

    def test_admin_create_client(self):
        res = self.client_http.post(
            "/v1/admin/clients",
            headers={"X-Admin-Key": "admin-secret-key"},
            json={"name": "New Client", "daily_bytes_quota": 5000000}
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertIn("api_key", data)
        self.assertEqual(data["name"], "New Client")

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_api_endpoints.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.endpoints'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/api/endpoints/__init__.py
# (empty)

# app/api/endpoints/extract.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.api.deps import get_current_client
from app.core.validator import validate_and_sanitize_url
from app.core.engines.ytdlp import YtDlpEngine
from app.core.engines.gallerydl import GalleryDlEngine

router = APIRouter()
ytdlp = YtDlpEngine()
gallerydl = GalleryDlEngine()

class ExtractRequest(BaseModel):
    url: str

@router.post("/extract")
def extract_metadata(body: ExtractRequest, client: dict = Depends(get_current_client)):
    clean_url = validate_and_sanitize_url(body.url)
    if "wikimedia.org" in clean_url:
        return gallerydl.extract_info(clean_url)
    return ytdlp.extract_info(clean_url)

# app/api/endpoints/tasks.py
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional
import time
from app.api.deps import get_current_client, get_task_repo, get_file_repo, get_client_repo
from app.core.validator import validate_and_sanitize_url
from app.core.errors import ErrorCode, ErrorEnvelope
from app.core.tokens import generate_download_token
from app.db.repositories import TaskRepository, FileRepository, ClientRepository

router = APIRouter()

class TaskCreateRequest(BaseModel):
    url: str
    format_id: Optional[str] = None
    max_height: Optional[int] = None
    audio_only: Optional[bool] = False

@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
def submit_task(
    body: TaskCreateRequest,
    client: dict = Depends(get_current_client),
    task_repo: TaskRepository = Depends(get_task_repo),
    client_repo: ClientRepository = Depends(get_client_repo)
):
    active_count = client_repo.get_active_task_count(client["id"])
    if active_count >= client["concurrency_limit"]:
        raise HTTPException(
            status_code=429,
            detail=ErrorEnvelope.create(ErrorCode.CONCURRENCY_LIMIT_EXCEEDED, "Concurrency limit reached", retryable=True).model_dump()
        )

    clean_url = validate_and_sanitize_url(body.url)
    task = task_repo.create_task(
        client_id=client["id"],
        url=clean_url,
        format_id=body.format_id,
        max_height=body.max_height,
        audio_only=body.audio_only
    )
    return {
        "id": task["id"],
        "status": task["status"],
        "progress": task["progress"],
        "estimated_bytes": task["estimated_bytes"],
        "created_at": task["created_at"]
    }

@router.get("/tasks/{task_id}")
def get_task_status(
    task_id: str,
    client: dict = Depends(get_current_client),
    task_repo: TaskRepository = Depends(get_task_repo),
    file_repo: FileRepository = Depends(get_file_repo)
):
    task = task_repo.get_task(task_id)
    if not task or task["client_id"] != client["id"]:
        raise HTTPException(status_code=404, detail=ErrorEnvelope.create(ErrorCode.NOT_FOUND, "Task not found").model_dump())

    files_output = []
    if task["status"] == "done":
        files = file_repo.get_files_by_task(task_id)
        now_exp = int(time.time()) + 900
        for f in files:
            token = generate_download_token(f["id"], now_exp)
            files_output.append({
                "id": f["id"],
                "filename": f["original_title"],
                "mime_type": f["mime_type"],
                "size_bytes": f["size_bytes"],
                "download_url": f"/v1/files/{f['id']}?token={token}",
                "expires_at": f["expires_at"]
            })

    return {
        "id": task["id"],
        "status": task["status"],
        "progress": task["progress"],
        "estimated_bytes": task["estimated_bytes"],
        "actual_bytes": task["actual_bytes"],
        "error_code": task["error_code"],
        "error_message": task["error_message"],
        "files": files_output
    }

@router.delete("/tasks/{task_id}")
def cancel_task(
    task_id: str,
    client: dict = Depends(get_current_client),
    task_repo: TaskRepository = Depends(get_task_repo)
):
    task = task_repo.get_task(task_id)
    if not task or task["client_id"] != client["id"]:
        raise HTTPException(status_code=404, detail=ErrorEnvelope.create(ErrorCode.NOT_FOUND, "Task not found").model_dump())

    task_repo.update_status(task_id, "cancelled")
    return {"id": task["id"], "status": "cancelled"}

# app/api/endpoints/files.py
import os
from fastapi import APIRouter, HTTPException, Query, Depends
from starlette.responses import FileResponse
from app.api.deps import get_file_repo
from app.core.tokens import verify_download_token
from app.core.errors import ErrorCode, ErrorEnvelope
from app.db.repositories import FileRepository

router = APIRouter()

@router.get("/files/{file_id}")
def download_file(
    file_id: str,
    token: str = Query(...),
    file_repo: FileRepository = Depends(get_file_repo)
):
    if not verify_download_token(token, file_id):
        raise HTTPException(status_code=401, detail=ErrorEnvelope.create(ErrorCode.TOKEN_INVALID, "Invalid or expired token").model_dump())

    file_record = file_repo.get_file(file_id)
    if not file_record:
        raise HTTPException(status_code=410, detail=ErrorEnvelope.create(ErrorCode.FILE_EXPIRED, "File has expired or been evicted").model_dump())

    path = file_record["storage_path"]
    if not os.path.exists(path):
        raise HTTPException(status_code=410, detail=ErrorEnvelope.create(ErrorCode.FILE_EXPIRED, "File missing from disk").model_dump())

    file_repo.update_last_accessed(file_id)
    return FileResponse(
        path=path,
        filename=file_record["original_title"],
        media_type=file_record["mime_type"]
    )

# app/api/endpoints/admin.py
import secrets
import hashlib
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from app.api.deps import require_admin, get_client_repo, get_file_repo
from app.db.repositories import ClientRepository, FileRepository

router = APIRouter()

class ClientCreateRequest(BaseModel):
    name: str
    daily_bytes_quota: int
    concurrency_limit: int = 1

@router.post("/admin/clients", status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreateRequest,
    admin_ok: bool = Depends(require_admin),
    client_repo: ClientRepository = Depends(get_client_repo)
):
    raw_api_key = f"key_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    client = client_repo.create_client(
        name=body.name,
        api_key_hash=key_hash,
        daily_bytes_quota=body.daily_bytes_quota,
        concurrency_limit=body.concurrency_limit
    )
    return {
        "id": client["id"],
        "name": client["name"],
        "api_key": raw_api_key,
        "daily_bytes_quota": client["daily_bytes_quota"],
        "concurrency_limit": client["concurrency_limit"]
    }

@router.get("/admin/storage")
def get_storage_stats(
    admin_ok: bool = Depends(require_admin),
    file_repo: FileRepository = Depends(get_file_repo)
):
    used = file_repo.get_total_storage_used()
    return {"used_bytes": used}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_api_endpoints.py`
Expected: Ran 2 tests in 0.08s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/api/endpoints/ tests/test_api_endpoints.py
git commit -m "feat(api): implement v1 endpoints for extract, tasks, files, and admin"
```

---

### Task 11: FastAPI Application Lifespan & Standardized Exception Handlers

**Files:**
- Create: `app/main.py`
- Test: `tests/test_main_app.py`

**Interfaces:**
- Consumes: All components.
- Produces:
  - `app`: Production-ready FastAPI ASGI application with WAL initialization, worker task dispatch, and global `AppException` handler.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main_app.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_main_app.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.network import setup_network_profile
from app.core.errors import AppException, ErrorEnvelope
from app.db.database import init_db, get_db_connection
from app.db.repositories import TaskRepository, FileRepository, ClientRepository
from app.core.storage import StorageManager
from app.core.worker import WorkerService
from app.api.endpoints.extract import router as extract_router
from app.api.endpoints.tasks import router as tasks_router
from app.api.endpoints.files import router as files_router
from app.api.endpoints.admin import router as admin_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_network_profile(settings.NETWORK_PROFILE)
    init_db()

    conn = get_db_connection()
    task_repo = TaskRepository(conn)
    task_repo.reset_running_tasks_on_startup()
    conn.close()

    storage_mgr = StorageManager()
    conn = get_db_connection()
    worker = WorkerService(
        task_repo=TaskRepository(conn),
        file_repo=FileRepository(conn),
        client_repo=ClientRepository(conn),
        storage_mgr=storage_mgr
    )
    app.state.worker = worker

    yield

    conn.close()

app = FastAPI(title="Media API", version="1.0.0", lifespan=lifespan)

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    envelope = ErrorEnvelope.create(code=exc.code, message=exc.message, retryable=exc.retryable)
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())

@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(extract_router, prefix="/v1")
app.include_router(tasks_router, prefix="/v1")
app.include_router(files_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_main_app.py`
Expected: Ran 1 test in 0.12s, OK.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_main_app.py
git commit -m "feat(main): wire fastapi application with lifespan and global exception handler"
```

---

### Task 12: Systemd Unit Deployment & End-to-End VM Verification

**Files:**
- Create: `deploy/media-api.service`
- Create: `deploy/setup_env.sh`
- Test: `tests/test_platform_matrix.py`

**Interfaces:**
- Consumes: Production environment in `/home/mediaapi/media-api`.
- Produces:
  - Running systemd service `media-api.service`.
  - Passing automated platform capability matrix tests.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_platform_matrix.py
import unittest
from app.core.validator import validate_and_sanitize_url
from app.core.errors import AppException, ErrorCode

class TestPlatformMatrix(unittest.TestCase):
    def test_allowlisted_platforms_pass_validator(self):
        urls = [
            "https://soundcloud.com/octobersveryown/drake-back-to-back-freestyle",
            "https://commons.wikimedia.org/wiki/File:Monarch_In_May.jpg"
        ]
        for u in urls:
            self.assertTrue(validate_and_sanitize_url(u))

    def test_non_goal_platforms_fail_validator(self):
        blocked = [
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            "https://x.com/NASA/status/1841893322122608930",
            "https://www.dailymotion.com/video/x7tgad0",
            "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
        ]
        for b in blocked:
            with self.assertRaises(AppException) as ctx:
                validate_and_sanitize_url(b)
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLATFORM)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails if anything is misconfigured**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest tests/test_platform_matrix.py`
Expected: PASS (or fail if allowlist doesn't match spec).

- [ ] **Step 3: Write minimal implementation**

```ini
# deploy/media-api.service
[Unit]
Description=Media API Daemon
After=network.target

[Service]
Type=simple
User=mediaapi
Group=mediaapi
WorkingDirectory=/home/mediaapi/media-api
EnvironmentFile=/home/mediaapi/media-api/.env
ExecStart=/home/mediaapi/media-api/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1

# Security & Sandboxing
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/hatch/media-api/data /home/hatch/media-api/storage /home/mediaapi/.cache
PrivateTmp=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# Resource Limits
MemoryMax=1G
CPUQuota=200%
LimitNOFILE=65536

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# deploy/setup_env.sh
#!/bin/bash
set -euo pipefail

mkdir -p /home/hatch/media-api/data
mkdir -p /home/hatch/media-api/storage/tmp
mkdir -p /home/hatch/media-api/storage/files
mkdir -p /home/mediaapi/.cache

chown -R mediaapi:mediaapi /home/hatch/media-api/data
chown -R mediaapi:mediaapi /home/hatch/media-api/storage
chown -R mediaapi:mediaapi /home/mediaapi/.cache

cp deploy/media-api.service /etc/systemd/system/media-api.service
systemctl daemon-reload
systemctl enable media-api.service
systemctl restart media-api.service
systemctl status media-api.service --no-pager
```

- [ ] **Step 4: Run full test suite to verify everything passes**

Run: `/home/mediaapi/media-api/venv/bin/python3 -m unittest discover tests`
Expected: Ran all tests, OK.

- [ ] **Step 5: Commit**

```bash
git add deploy/ tests/test_platform_matrix.py
git commit -m "feat(deploy): add systemd service, setup script, and platform matrix test"
```

