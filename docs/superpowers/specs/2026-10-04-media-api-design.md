# Media API Architecture & Specification (v1)

- **Date**: 2026-10-04
- **Status**: Draft (Approved for Specification Review)
- **Runtime Environment**: Meta Muse Linux VM (Debian-based, Kernel 7.0, Non-root mediaapi, UID 1000)
- **Root Directory**: /home/mediaapi/media-api (symlink to persistent /home/hatch/media-api)

---

## 1. System Overview & Core Objectives

Media API is a high-performance, hardened media extraction and download service. It provides a RESTful interface for extracting metadata, downloading single media items (audio/video/images), transcoding audio formats via ffmpeg, and serving files with HTTP Range streaming support.

### 1.1 Core Objectives
1. **Persistent Isolated Storage**: Storage and database reside on persistent volume (/home/hatch) capped strictly at 20 GB with automated LRU watermark eviction.
2. **Network Portability via Network Profiles**: Seamless operation across both constrained sandbox proxies (Meta Muse VM) and standard egress hosts via NETWORK_PROFILE=sandbox|standard.
3. **v1 Platform Scope**:  Platform GET-only yang sudah teruji (strictly tested GET-only platforms).
4. **Hardened Process & SSRF Isolation**: Pure zero-I/O URL validation, process-group isolation (os.killpg), non-root systemd execution (ProtectSystem=strict), and atomic filesystem state commits.

### 1.2 Explicit Non-Goals (v1)
To ensure system stability, security, and predictability under VM firewall policies:
- **Direct Links**: Non-goal for v1. Direct .m3u8, .mp4, or raw media URLs are handled by yt-dlp's generic extractor. The generic extractor is strictly disabled (--use-extractors default,-generic) because it crawls arbitrary web pages, follows embeds across domains, and acts as an open proxy/SSRF vector. Only allowlisted platforms with dedicated extractors are accepted.
- **YouTube & Outbound POST Services**: Non-goal for the v1 sandbox profile. YouTube requires HTTP POST requests to the Innertube player API (/youtubei/v1/player). Sandbox firewall policy drops non-GitHub POST requests. In accordance with policy compliance, no workaround relays or wrapping techniques are used.
- **Live Streams**: Strictly rejected (--match-filters !is_live), returning error unsupported_stream.
- **Playlists & Channels**: Pure playlists and channel URLs are detected on extraction (_type == playlist) and rejected with error unsupported_playlist. CLI flags --playlist-items 1 and --max-downloads 1 provide defense-in-depth. Gallery-dl is restricted via URL regex to single-post views only.
- **DRM Protected Media**: Widevine/FairPlay encrypted media is rejected immediately.
- **Authentication / Login**: No user credentials or session cookies are stored. Platforms requiring account login (e.g., Reddit, Vimeo) are rejected with upstream_blocked.
- **Deno in Sandbox Profile**: Deno is excluded from sandbox runtime requirements (was only relevant for YouTube challenges).

---

## 2. Network Profile Architecture (NETWORK_PROFILE)

Outbound HTTP routing is abstracted through an engine network layer to allow seamless portability between the Meta Muse VM sandbox and standard hosting environments.

`
                      +-----------------------------+
                      |       Media API Engine      |
                      +-----------------------------+
                                     |
               +---------------------+---------------------+
               |                                           |
      [NETWORK_PROFILE=sandbox]                   [NETWORK_PROFILE=standard]
               |                                           |
    +--------------------------+                +-----------------------+
    | Subprocess /usr/bin/curl |                | Native Python Sockets |
    |   - Fail-fast non-GET    |                |   - Full HTTP methods |
    |   - Connection: close    |                |   - Keep-alive pooled |
    |   - Streamed stdout      |                |   - Optional Deno     |
    +--------------------------+                +-----------------------+
`

### 2.1 Sandbox Profile Specification
- **Egress Constraints**: The VM firewall inspects /proc/16940/exe and only allows outbound traffic from /usr/bin/curl and /usr/bin/wget. Outbound POST is permitted only to github.com.
- **yt-dlp Integration (CurlRH)**: Custom RequestHandler extending yt_dlp.networking.common.RequestHandler.
  - Registered with yt_dlp.networking.common.register_rh(CurlRH).
  - Injected as default handler in YoutubeDL._request_director.
- **gallery-dl Integration (CurlAdapter)**: Custom adapter mounted via monkey-patching gallery_dl.extractor.common._build_requests_adapter.
- **Fail-Fast on Non-GET**: Any outbound request with HTTP method other than GET or HEAD is immediately terminated inside the handler without invoking subprocess or waiting for timeouts, raising TransportError(Egress POST blocked by policy) mapped to error code egress_post_blocked.
- **Subprocess Arguments**: Every curl call specifies --connect-timeout 10, --max-time 30, and -H Connection: close.
- **Memory Streaming Guarantee**: For binary file downloads, responses stream from subprocess stdout in 64 KB chunks rather than buffering into RAM, keeping Python memory consumption flat (<60 MB). External downloader --downloader curl and native HLS write directly to temporary disk storage.

### 2.2 Standard Profile Specification
- Standard networking using Python urllib3 / equests with persistent connection pooling and full HTTP verb support.

---

## 3. Platform Capability Matrix (v1 Verified)

Every platform in the allowlist is empirically verified against the sandbox profile on the VM. Platforms failing empirical tests are blocked at the validator before entering the task queue.

| Platform | Engine | Test Status | Mechanism | Error Code if Blocked |
|---|---|---|---|---|
| **SoundCloud** | yt-dlp | **VERIFIED PASS** | GET metadata + native HLS + ffmpeg container fix (~36s / 3.4 MB) | - |
| **Wikimedia Commons** | gallery-dl | **VERIFIED PASS** | GET metadata + streamed image download (~2s / 605 KB) | - |
| **YouTube** | yt-dlp | **BLOCKED (SANDBOX)** | Webpage GET succeeds, but player API requires POST to Innertube | egress_post_blocked |
| **X / Twitter** | yt-dlp | **BLOCKED (SANDBOX)** | Guest token requires POST to pi.x.com/1.1/guest/activate.json | egress_post_blocked |
| **Dailymotion** | yt-dlp | **BLOCKED (SANDBOX)** | Metadata extraction requires POST | egress_post_blocked |
| **Twitch Clips** | yt-dlp | **BLOCKED (SANDBOX)** | Metadata extraction requires GraphQL POST | egress_post_blocked |
| **Reddit** | yt-dlp | **BLOCKED (UPSTREAM)**| Account authentication required; refuses unauthenticated access | upstream_blocked |
| **TikTok** | yt-dlp | **BLOCKED (UPSTREAM)**| Datacenter IP blocked by upstream anti-bot | upstream_blocked |
| **Vimeo** | yt-dlp | **BLOCKED (UPSTREAM)**| Web client requires user login/cookies | upstream_blocked |
| **Direct Links (.m3u8/.mp4)**| yt-dlp | **BLOCKED (NON-GOAL)**| Generic extractor disabled (-generic) for open proxy prevention | unsupported_platform |

*Acceptance Rule*: The domain allowlist in the validator mirrors this table exactly. Any domain not marked VERIFIED PASS is rejected immediately. The matrix is codified in 	ests/test_platform_matrix.py and must pass on engine updates.

---

## 4. Pure Zero-I/O SSRF & Input Validator

To guarantee that Python processes never hang on blocked DNS sockets, URL validation executes purely in-memory with zero network or DNS I/O.

### 4.1 Validation Pipeline
1. **Scheme Validation**: Scheme must be strictly https.
2. **Userinfo Rejection**: URLs containing @ (e.g. https://user:pass@host) are rejected immediately.
3. **Port Enforcement**: Port must be omitted or explicitly 443.
4. **IP Literal Rejection**: Input hostname is validated against IPv4 dot-decimal regex, IPv6 bracketed regex, and integer/hex IP formats. Any IP literal is rejected.
5. **Domain Allowlist Check**: Hostname must match the verified platform domain allowlist:
   - soundcloud.com, *.soundcloud.com
   - commons.wikimedia.org, upload.wikimedia.org
6. **Path & Query Single-Item Sanitization**:
   - Rejects URLs matching playlist, channel, or album aggregation patterns (e.g. /sets/, /user/, /channel/).
   - Strips tracking query parameters (si, utm_*, ef).
7. **Rejection Responses**:
   - SSRF attempt / IP literal: {error: {code: ssrf_detected, message: Invalid or restricted URL format, retryable: false}} (HTTP 400).
   - Domain outside allowlist: {error: {code: unsupported_platform, message: Domain is not in the verified allowlist, retryable: false}} (HTTP 400).

---

## 5. Storage Engine & Automated Eviction

### 5.1 Filesystem Layout
All storage resides on persistent volume /home/hatch within the same filesystem to ensure atomic os.replace operations:
- data/media.db: SQLite database (WAL mode).
- storage/tmp/{task_id}/: Working directory for active downloads. Cleaned on task completion or failure.
- storage/files/{file_id}.{ext}: Final immutable media files.

### 5.2 Quota & Watermark Eviction
- **Hard Storage Cap**: 20.0 GB (STORAGE_MAX_BYTES = 20 * 1024 * 1024 * 1024).
- **High Watermark (90%)**: 18.0 GB triggers background cleaner.
- **Low Watermark (70%)**: 14.0 GB target threshold for cleaner eviction.
- **File Expiration & Eviction Rules**:
  - Default TTL: 24 hours from download completion.
  - Grace Period: Files accessed within the last 10 minutes (last_accessed_at > now - 600s) are protected from eviction unless total storage exceeds 95% (emergency mode).
  - Eviction Order: Lowest last_accessed_at timestamp.
  - On eviction: File unlinked from disk, DB record marked expired. Subsequent requests return HTTP 410 Gone.
- **Disk Reservation Rule**:
  - Tasks in preparing state must reserve 2.2 * estimated_bytes before entering unning.
  - If current storage + reservation exceeds 90%, synchronous eviction runs down to 70%. If still insufficient, task fails with quota_exceeded.

---

## 6. Database Schema & State Transitions

### 6.1 SQLite Configuration
- Location: data/media.db
- Journal Mode: WAL (Write-Ahead Logging for high concurrent read performance)
- Pragmas: PRAGMA busy_timeout = 5000;, PRAGMA foreign_keys = ON;, PRAGMA synchronous = NORMAL;

### 6.2 Schema Definition

`sql
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,                       -- e.g. cli_01h7x... (ULID or UUID hex)
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,         -- SHA-256 hash of API key
    daily_bytes_quota INTEGER NOT NULL,        -- e.g. 5368709120 (5 GB)
    daily_bytes_used INTEGER NOT NULL DEFAULT 0,
    concurrency_limit INTEGER NOT NULL DEFAULT 1,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,                       -- tsk_ + 128-bit hex (32 chars)
    client_id TEXT NOT NULL REFERENCES clients(id),
    url TEXT NOT NULL,
    status TEXT NOT NULL,                      -- queued, preparing, running, done, failed, cancelled
    format_id TEXT,
    max_height INTEGER,
    audio_only INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0.0,        -- 0.0 to 100.0
    estimated_bytes INTEGER,                   -- Nullable, resolved in preparing
    reserved_bytes INTEGER NOT NULL DEFAULT 0,
    actual_bytes INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,                           -- standardized error code
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,                       -- fl_ + 128-bit hex (32 chars)
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    client_id TEXT NOT NULL REFERENCES clients(id),
    filename TEXT NOT NULL,                    -- disk name:  
    original_title TEXT NOT NULL,              -- sanitised title for Content-Disposition
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL              -- created_at + 24 hours
);

CREATE INDEX IF NOT EXISTS idx_tasks_client_status ON tasks(client_id, status);
CREATE INDEX IF NOT EXISTS idx_files_client_id ON files(client_id);
CREATE INDEX IF NOT EXISTS idx_files_expires_at ON files(expires_at);
CREATE INDEX IF NOT EXISTS idx_files_last_accessed ON files(last_accessed_at);
`

### 6.3 Task State Machine

`
      [ POST /v1/tasks ]
              |
              v
          ( queued ) <---------------------+
              |                            |  (Worker restarts: running tasks
              v (Worker picks task)        |   reset to failed; queued re-evaluated)
        ( preparing ) ---------------------+
              | (Extract OK, size known, quota reserved)
              v
          ( running )
              |
              +--------------------------+-------------------------+
              |                          |                         |
              v (Atomic move + DB)       v (Error/Timeout/Stall)   v (DELETE /v1/tasks/{id})
           ( done )                  ( failed )               ( cancelled )
              |
              v (Cleaner: TTL / LRU Evict)
          ( expired / 410 )
`

- **Startup Recovery**: On worker startup, any tasks left in unning or preparing states are updated to ailed (error_code= system_restart). queued tasks remain in queue.
- **Cancellation**: DELETE /v1/tasks/{id} triggers os.killpg on the task's process group, unlinks storage/tmp/{task_id}/, releases reserved quota, and sets state to cancelled.

---

## 7. Execution Engine & Worker Subsystem

### 7.1 Queue & Semaphore Control
- **Global Concurrency Semaphore**: syncio.Semaphore(3) for concurrent downloads.
- **Dedicated Extract Semaphore**: syncio.Semaphore(2) prevents extract requests from blocking download slots.
- **Extract In-Memory Cache**: LRU cache (TTL 300s) for metadata of identical URLs.
- **Global Queue Depth**: Capped at 50 tasks. Submissions exceeding capacity receive HTTP 429 ({error: {code: queue_full, message: Server queue capacity reached, retryable: true}}, Retry-After: 30).
- **Per-Client Concurrency**: Enforced by querying active tasks count (preparing + unning) against client.concurrency_limit. Returns HTTP 429 if exceeded.

### 7.2 Subprocess Isolation & Watchdog
- **Process Spawning**: Subprocesses run via syncio.create_subprocess_exec with start_new_session=True.
- **Command Sanitization**: Arguments are passed strictly as lists of strings (no shell=True).
- **Duration Watchdog**: Task execution hard limit is 15 minutes (TASK_MAX_DURATION = 900s).
- **Stall Watchdog**: Monitors filesystem activity in storage/tmp/{task_id}/. If directory size does not increase for 60 seconds, process group is terminated with os.killpg(proc.pid, signal.SIGTERM) (followed by SIGKILL after 5s) and flagged as 	ask_stalled.
- **Progress Calculation**: Computed by watchdog as (tmp_dir_bytes / estimated_bytes) * 100, clamped to 99.0% until atomic move completes, at which point progress becomes 100.0%.

---

## 8. Authentication & Download Tokens

### 8.1 Client Authentication
- Header: X-API-Key: {plain_key}
- Comparison: Constant-time hash verification against clients.api_key_hash using hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), stored_hash).
- Client Scoping: Clients can only inspect, cancel, and access tasks and files created under their own client_id. Accessing another client's task returns HTTP 404.

### 8.2 Stateless HMAC Download Tokens
- Eliminates mutable database tokens table and avoids race conditions.
- Format: 	oken = base64url(file_id + . + exp + . + HMAC_SHA256(SECRET, file_id + . + exp))
- TTL: 15 minutes (TOKEN_TTL = 900s), clamped so it cannot exceed iles.expires_at.
- Minting: Generated on GET /v1/tasks/{id} when task status is done.
- Validation: Verifies expiration and signature in constant-time.
- Access Log Protection: Uvicorn and reverse proxies are configured to mask or strip ?token= from access logs to prevent token leakage.

---

## 9. API Specification & Error Contracts

### 9.1 Standard Error Envelope
All error responses adhere strictly to the schema:
`json
{
  error: {
    code: unsupported_platform,
    message: Domain is not in the verified allowlist,
    retryable: false
  }
}
`

#### Standard Error Codes:
- ssrf_detected (400, false): IP literal, userinfo, or invalid scheme detected.
- unsupported_platform (400, false): Domain not in verified capability allowlist.
- unsupported_playlist (400, false): Pure playlist or channel URL rejected.
- unsupported_stream (400, false): Live stream detected.
- upstream_blocked (502, false): Upstream geo-blocked, IP-banned, or login required.
- egress_post_blocked (502, false): Upstream requires POST, dropped by sandbox policy.
- concurrency_limit_exceeded (429, true): Client active tasks limit reached.
- queue_full (429, true): Global queue limit reached.
- daily_quota_exceeded (429, false): Client daily byte quota exhausted.
- quota_exceeded (507, false): Server storage full; eviction could not free enough space.
- 	ask_timeout (504, false): Execution exceeded 15 minutes limit.
- 	ask_stalled (504, true): Download stalled with no progress for 60 seconds.
- 	ask_cancelled (410, false): Task was cancelled by client.
- ile_expired (410, false): File evicted or TTL reached.
- 	oken_invalid (401, false): Download token expired or signature invalid.

### 9.2 Endpoint Definitions

#### 1. Extract Metadata: POST /v1/extract
Extracts information without downloading.
- **Request**:
  `json
  {
    url: https://soundcloud.com/artist/track
  }
  `
- **Response** (200 OK):
  `json
  {
    title: Track Title,
    uploader: Artist Name,
    duration: 215,
    thumbnail: https://...,
    estimated_bytes: 3560000,
    formats: [
      {
        format_id: hls_aac_160k,
        ext: m4a,
        quality: 160k,
        has_video: false,
        has_audio: true,
        filesize_approx: 3560000
      }
    ]
  }
  `

#### 2. Submit Task: POST /v1/tasks
Enqueues a media download task.
- **Request**:
  `json
  {
    url: https://soundcloud.com/artist/track,
    format_id: hls_aac_160k,
    audio_only: true,
    audio_format: mp3
  }
  `
- **Response** (202 Accepted):
  `json
  {
    id: tsk_01923847a9b8c7d6e5f4a3b2c1d0e9f8,
    status: queued,
    progress: 0.0,
    estimated_bytes: null,
    created_at: 2026-10-04T16:00:00Z
  }
  `

#### 3. Poll Task Status: GET /v1/tasks/{id}
Returns task state, progress, and download links if done.
- **Response** (200 OK):
  `json
  {
    id: tsk_01923847a9b8c7d6e5f4a3b2c1d0e9f8,
    status: done,
    progress: 100.0,
    estimated_bytes: 3560000,
    actual_bytes: 3481200,
    files: [
      {
        id: fl_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6,
        filename: Track_Title.mp3,
        mime_type: audio/mpeg,
        size_bytes: 3481200,
        download_url: /v1/files/fl_a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6?token=eyJhbGciOi...,
        expires_at: 2026-10-05T16:00:00Z
      }
    ]
  }
  `

#### 4. Cancel Task: DELETE /v1/tasks/{id}
Cancels an active or queued task.
- **Response** (200 OK):
  `json
  {
    id: tsk_01923847a9b8c7d6e5f4a3b2c1d0e9f8,
    status: cancelled
  }
  `

#### 5. Stream / Download File: GET /v1/files/{id}?token={token}
Streams the binary file using Starlette FileResponse with HTTP Range header support.
- Requires valid, non-expired HMAC token.
- Returns 200 OK or 206 Partial Content.
- Updates iles.last_accessed_at timestamp.

---

## 10. Hardened Systemd Service Configuration

Service unit installed at /etc/systemd/system/media-api.service:

`ini
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
`

---

## 11. Acceptance Criteria & Test Plan

1. **Pure SSRF Test Suite (	ests/test_ssrf.py)**:
   - Rejection of IP literals (127.0.0.1, 10.0.0.1, ::1, 169.254.169.254).
   - Rejection of non-HTTPS schemes (http, ile, gopher, tp).
   - Rejection of userinfo, non-443 ports, and unallowlisted domains without performing DNS queries.
2. **Platform Matrix Test Suite (	ests/test_platform_matrix.py)**:
   - Verification that verified platforms succeed under CurlRH / CurlAdapter.
   - Verification that non-GET requests fail-fast with egress_post_blocked.
   - Verification that pure playlists trigger unsupported_playlist.
3. **Storage & Eviction Test Suite (	ests/test_cleaner.py)**:
   - 20 GB capacity check and watermark threshold triggering (90% down to 70%).
   - Grace period verification (recently accessed files protected).
   - Atomic rename and single-filesystem verification.
4. **Lifecycle & Concurrency Test Suite (	ests/test_tasks.py)**:
   - Concurrency limit and semaphore slot contention.
   - Watchdog process kill (os.killpg) on cancellation and timeout.
   - Clean failure state transition on ungraceful worker restart.
5. **Download Token Test Suite (	ests/test_tokens.py)**:
   - Stateless HMAC signature and expiration verification.
   - 410 Gone return after file eviction.
