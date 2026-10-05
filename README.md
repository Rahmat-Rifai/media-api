# Media API (v1)

A persistent, hardened media extraction and download REST service built with **FastAPI**, **yt-dlp**, **gallery-dl**, **ffmpeg**, and **SQLite WAL**, designed specifically to operate reliably under strict sandbox egress constraints (GET/HEAD-only, proxy binary enforcement) with automated 20 GB watermark eviction.

---

## Key Features

- **Strict Sandbox Egress Abstraction**: Routes upstream traffic via `/usr/bin/curl` with immediate fail-fast on non-GET/HEAD methods, preventing network stalls.
- **Zero-I/O SSRF Protection**: Pure lexical URL parser rejecting non-HTTPS, non-443 ports, IP literals, credentials, and non-allowlisted domains without DNS lookups.
- **Storage Management & LRU Eviction**: Strictly capped at 20 GB on persistent storage with high-watermark (90%) triggering eviction down to low-watermark (70%) based on LRU access time, with a 10-minute grace period.
- **Stateless HMAC-SHA256 Download Tokens**: 15-minute expiring download tokens clamped to file TTL.
- **SQLite WAL Mode**: Single-writer multiple-reader architecture with foreign keys and busy timeouts for robust persistence across service restarts.
- **Hardened Deployment**: systemd unit running as unprivileged `mediaapi` user with `ProtectSystem=strict`, `NoNewPrivileges=true`, and `MemoryMax=1G`.

---

## Architecture & API Endpoints

### Public Endpoints
- `POST /v1/extract`: Extract metadata and stream formats without downloading (`X-API-Key` required).
- `POST /v1/tasks`: Submit asynchronous media extraction/download task (`X-API-Key` required).
- `GET /v1/tasks/{id}`: Poll task status, download progress, and signed file URLs (`X-API-Key` required).
- `DELETE /v1/tasks/{id}`: Cancel a queued or active task (`X-API-Key` required).
- `GET /v1/files/{id}?token={hmac_token}`: Stream or download completed media file with HTTP Range support.

### Admin Endpoints
- `POST /v1/admin/clients`: Register new API client with custom daily byte quotas (`X-Admin-Key` required).
- `GET /v1/admin/storage`: Query total storage utilization (`X-Admin-Key` required).
- `POST /v1/admin/cleaner/run`: Trigger manual storage eviction cycle (`X-Admin-Key` required).

---

## Getting Started

### Prerequisites
- Python 3.12+
- ffmpeg
- curl

### Installation
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Running Tests
```bash
python3 -m unittest discover tests
```

### Starting Service
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

---

## License
MIT

