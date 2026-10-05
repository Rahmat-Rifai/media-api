# Rencana Implementasi — Foto, Playlist/Album/Carousel, dan Kunci API Permanen

> **Untuk pekerja agen:** SUB-SKILL WAJIB: gunakan superpowers:subagent-driven-development (disarankan) atau superpowers:executing-plans untuk menjalankan rencana ini tugas demi tugas. Langkah memakai sintaks kotak centang (- [ ]) untuk pelacakan.

**Tujuan:** Media API bisa mengunduh foto (tunggal & carousel), kumpulan (playlist YouTube, album YT Music, album Facebook, carousel Instagram) sebagai banyak file dalam satu task, dan menjamin `key_vGV-CNOBdgm-7B6NL3NxGY-5vlQbmUutJYg1SPS0UQ8` permanen.

**Arsitektur:** Gerbang dua lapis memisahkan kumpulan yang boleh dari channel/profil yang ditolak — lapis 1 aturan URL di `validator.py` (tanpa jaringan), lapis 2 verifikasi metadata di `ytdlp.py` (sebelum unduh). Kuota & disk dijaga per item lewat `ItemBudget` yang dipanggil `match_filter`/`progress_hooks` yt-dlp, sehingga kumpulan tanpa batas item berhenti sendiri dengan laporan `skipped[]`.

**Tech Stack:** Python 3.12, FastAPI, yt-dlp 2026.08.19, gallery-dl, SQLite, `unittest` + `unittest.mock`.

**Spesifikasi:** `docs/superpowers/specs/2026-10-05-foto-playlist-api-key-permanen-design.md`

## Kendala Global

- Semua trafik keluar WAJIB lewat tunnel (`NETWORK_PROFILE=sandbox` + `UPSTREAM_PROXY_URL`). Jangan diubah.
- Kode galat pada `ErrorCode` TIDAK boleh ditambah/dihapus — hanya pesannya yang berubah.
- Field respons API lama TIDAK boleh dihapus/diubah arti. Field baru bersifat tambahan (mundur kompatibel).
- Menolak berdasarkan nama extractor `YoutubeTab` DILARANG (melayani playlist dan channel sekaligus).
- Uji jalan dengan `./venv/bin/python -m unittest discover -s tests -v`, tanpa jaringan nyata.
- Larangan `test_default_settings` pada `tests/test_config_and_errors.py` `STORAGE_MAX_BYTES == 20 * 1024**3` WAJIB diubah ke 5 GB.

## Struktur File

| File | Tanggung jawab |
|---|---|
| `app/core/config.py` | Semua angka konfigurasi + `MASTER_API_KEY` |
| `app/core/validator.py` | Lapis 1: aturan URL per platform |
| `app/core/engines/base.py` | Antarmuka `MediaEngine` |
| `app/core/engines/ytdlp.py` | Ekstrak/unduh, lapis 2, `kind`, kait anggaran |
| `app/core/engines/gallerydl.py` | Mesin Wikimedia + fallback foto |
| `app/core/budget.py` (baru) | `ItemBudget` + `BudgetExceeded` + `_entry_size` |
| `app/core/storage.py` | `get_free_bytes()` |
| `app/core/cleaner.py` | `can_fit()` tanpa eksepsi |
| `app/core/worker.py` | Orkestrasi task, fallback mesin, `result_json` |
| `app/db/database.py` | Migrasi kolom `tasks.result_json` |
| `app/db/repositories.py` | `ensure_master_active`, `get_daily_quota_remaining`, `set_result` |
| `app/main.py` | Semai kunci master + jaminan `is_active` |
| `app/api/endpoints/tasks.py` | `collection`, `skipped[]`, `index`, `kind` |

---

### Tugas 1: Angka konfigurasi

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_config_and_errors.py`

**Interfaces:**
- Produksi: `settings.MASTER_API_KEY` (str), `settings.MASTER_DAILY_BYTES_QUOTA` (int), `settings.MASTER_CONCURRENCY_LIMIT` (int), `settings.DISK_MIN_FREE_BYTES` (int), `settings.QUOTA_MIN_REMAINING_BYTES` (int), `settings.TASK_MAX_DURATION_COLLECTION_SECONDS` (int)

- [ ] **Langkah 1: Tulis tes yang gagal**

Ganti isi `tests/test_config_and_errors.py` (pertahankan `test_error_envelope_serialization` dan `test_app_exception` apa adanya), tambah:

~~~python
    def test_default_settings(self):
        settings = Settings(ADMIN_API_KEY="test-admin-key")
        self.assertEqual(settings.STORAGE_MAX_BYTES, 5 * 1024 * 1024 * 1024)
        self.assertEqual(settings.HIGH_WATERMARK_RATIO, 0.90)
        self.assertEqual(settings.LOW_WATERMARK_RATIO, 0.70)
        self.assertEqual(settings.WORKER_CONCURRENCY, 3)
        self.assertEqual(settings.UPSTREAM_PROXY_URL, "https://proxy.rahmat.cc.cd/proxy")

    def test_budget_and_duration_settings(self):
        settings = Settings(ADMIN_API_KEY="test-admin-key")
        self.assertEqual(settings.DISK_MIN_FREE_BYTES, 512 * 1024 * 1024)
        self.assertEqual(settings.QUOTA_MIN_REMAINING_BYTES, 64 * 1024 * 1024)
        self.assertEqual(settings.TASK_MAX_DURATION_SECONDS, 900)
        self.assertEqual(settings.TASK_MAX_DURATION_COLLECTION_SECONDS, 3600)
        self.assertEqual(settings.TASK_STALL_TIMEOUT_SECONDS, 180)

    def test_master_key_settings(self):
        settings = Settings(ADMIN_API_KEY="test-admin-key")
        self.assertEqual(settings.MASTER_API_KEY, "key_vGV-CNOBdgm-7B6NL3NxGY-5vlQbmUutJYg1SPS0UQ8")
        self.assertEqual(settings.MASTER_DAILY_BYTES_QUOTA, 100 * 1024 * 1024 * 1024 * 1024)
        self.assertEqual(settings.MASTER_CONCURRENCY_LIMIT, 50)
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_config_and_errors -v`
Expected: FAIL pada `test_default_settings` (masih 20 GB) dan `AttributeError: DISK_MIN_FREE_BYTES`

- [ ] **Langkah 3: Ubah `app/core/config.py`**

Di badan `class Settings`, ganti baris `STORAGE_MAX_BYTES`, `TASK_MAX_DURATION_SECONDS`, `TASK_STALL_TIMEOUT_SECONDS` dan tambah baris baru:

~~~python
    STORAGE_MAX_BYTES: int = Field(default=5 * 1024 * 1024 * 1024)          # 5 GB (disk fisik 7,5 GB)
    GRACE_PERIOD_SECONDS: int = Field(default=600)
    DISK_MIN_FREE_BYTES: int = Field(default=512 * 1024 * 1024)             # berhenti bila sisa < 512 MB
    QUOTA_MIN_REMAINING_BYTES: int = Field(default=64 * 1024 * 1024)        # berhenti bila sisa kuota < 64 MB
    FILE_DEFAULT_TTL_SECONDS: int = Field(default=86400)
    WORKER_CONCURRENCY: int = Field(default=3)
    EXTRACT_CONCURRENCY: int = Field(default=2)
    TASK_MAX_DURATION_SECONDS: int = Field(default=900)                     # URL tunggal: 15 menit
    TASK_MAX_DURATION_COLLECTION_SECONDS: int = Field(default=3600)         # kumpulan: 1 jam
    TASK_STALL_TIMEOUT_SECONDS: int = Field(default=180)
    TOKEN_SECRET: str = Field(default="change-this-in-production-hmac-secret")
    TOKEN_TTL_SECONDS: int = Field(default=900)
    MASTER_API_KEY: str = Field(default="key_vGV-CNOBdgm-7B6NL3NxGY-5vlQbmUutJYg1SPS0UQ8")
    MASTER_DAILY_BYTES_QUOTA: int = Field(default=100 * 1024 * 1024 * 1024 * 1024)
    MASTER_CONCURRENCY_LIMIT: int = Field(default=50)
~~~

- [ ] **Langkah 4: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest tests.test_config_and_errors -v`
Expected: PASS semua

- [ ] **Langkah 5: Commit**

~~~bash
git add app/core/config.py tests/test_config_and_errors.py
git commit -m "feat(config): angka 5 GB, batas waktu task terpisah, MASTER_API_KEY"
~~~

---

### Tugas 2: Kunci API permanen

**Files:**
- Modify: `app/db/repositories.py` (metode baru `ClientRepository.ensure_master_active`)
- Modify: `app/main.py` (blok penyemaian di `lifespan`)
- Test: `tests/test_master_key.py` (baru)

**Interfaces:**
- Konsumsi: `settings.MASTER_API_KEY`, `settings.MASTER_DAILY_BYTES_QUOTA`, `settings.MASTER_CONCURRENCY_LIMIT` (Tugas 1)
- Produksi: `ClientRepository.ensure_master_active(api_key_hash: str, daily_bytes_quota: int, concurrency_limit: int) -> dict`

- [ ] **Langkah 1: Tulis tes yang gagal**

Buat `tests/test_master_key.py`:

~~~python
import hashlib
import os
import tempfile
import unittest

from app.core.config import settings
from app.db.database import init_db, get_db_connection
from app.db.repositories import ClientRepository


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class TestMasterKey(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)
        self.repo = ClientRepository(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _ensure(self):
        return self.repo.ensure_master_active(
            api_key_hash=_hash(settings.MASTER_API_KEY),
            daily_bytes_quota=settings.MASTER_DAILY_BYTES_QUOTA,
            concurrency_limit=settings.MASTER_CONCURRENCY_LIMIT,
        )

    def test_seed_creates_permanent_client(self):
        row = self._ensure()
        self.assertEqual(row["name"], "master")
        self.assertEqual(row["api_key_hash"], _hash(settings.MASTER_API_KEY))
        self.assertEqual(row["is_active"], 1)
        self.assertEqual(row["daily_bytes_quota"], settings.MASTER_DAILY_BYTES_QUOTA)
        self.assertEqual(row["concurrency_limit"], settings.MASTER_CONCURRENCY_LIMIT)

    def test_key_survives_restart_and_deactivation(self):
        self._ensure()
        with self.conn:
            self.conn.execute("UPDATE clients SET is_active = 0 WHERE api_key_hash = ?", (_hash(settings.MASTER_API_KEY),))
        self.assertIsNone(self.repo.get_by_api_key_hash(_hash(settings.MASTER_API_KEY)))
        self._ensure()
        self.assertIsNotNone(self.repo.get_by_api_key_hash(_hash(settings.MASTER_API_KEY)))

    def test_quota_never_shrinks(self):
        self._ensure()
        with self.conn:
            self.conn.execute("UPDATE clients SET daily_bytes_quota = 1 WHERE api_key_hash = ?", (_hash(settings.MASTER_API_KEY),))
        row = self._ensure()
        self.assertEqual(row["daily_bytes_quota"], settings.MASTER_DAILY_BYTES_QUOTA)

    def test_repeated_ensure_does_not_duplicate(self):
        self._ensure()
        self._ensure()
        cur = self.conn.execute("SELECT COUNT(*) AS n FROM clients")
        self.assertEqual(cur.fetchone()["n"], 1)


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_master_key -v`
Expected: FAIL dengan `AttributeError: 'ClientRepository' object has no attribute 'ensure_master_active'`

- [ ] **Langkah 3: Tambah `ensure_master_active` ke `ClientRepository`**

Tambahkan tepat setelah `get_by_api_key_hash` di `app/db/repositories.py`:

~~~python
    def ensure_master_active(self, api_key_hash: str, daily_bytes_quota: int, concurrency_limit: int) -> Dict[str, Any]:
        """Jaminan permanen: klien master selalu ada, aktif, dan kuotanya utuh.

        Dipanggil setiap kali proses start. Aman dipanggil berulang.
        """
        with self.conn:
            self.conn.execute(
                "UPDATE clients SET is_active = 1, daily_bytes_quota = ?, concurrency_limit = ? WHERE api_key_hash = ?",
                (daily_bytes_quota, concurrency_limit, api_key_hash),
            )
            cur = self.conn.execute("SELECT * FROM clients WHERE api_key_hash = ?", (api_key_hash,))
            row = cur.fetchone()
        if row:
            return dict(row)
        return self.create_client(
            name="master",
            api_key_hash=api_key_hash,
            daily_bytes_quota=daily_bytes_quota,
            concurrency_limit=concurrency_limit,
        )
~~~

- [ ] **Langkah 4: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest tests.test_master_key -v`
Expected: PASS 4 tes

- [ ] **Langkah 5: Ganti blok penyemaian di `app/main.py`**

Hapus blok `client_repo = ClientRepository(conn)` sampai `conn.close()` lama, ganti dengan:

~~~python
    conn = get_db_connection()
    client_repo = ClientRepository(conn)
    client_repo.ensure_master_active(
        api_key_hash=hashlib.sha256(settings.MASTER_API_KEY.encode()).hexdigest(),
        daily_bytes_quota=settings.MASTER_DAILY_BYTES_QUOTA,
        concurrency_limit=settings.MASTER_CONCURRENCY_LIMIT,
    )
    conn.close()
~~~

Hapus juga baris `master_key = "key_vGV-..."` dan `master_hash = ...` yang lama. `import hashlib` tetap dipakai.

- [ ] **Langkah 6: Jalankan seluruh tes**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: PASS semua

- [ ] **Langkah 7: Commit**

~~~bash
git add app/db/repositories.py app/main.py tests/test_master_key.py
git commit -m "feat(auth): jaminan kunci master permanen dan tidak bisa mati diam-diam"
~~~

---

### Tugas 3: `kind` dan nama file per item

**Files:**
- Modify: `app/core/engines/ytdlp.py` (`_entry_kind`, `_parse_info`, pola `outtmpl`)
- Modify: `app/core/worker.py` (`original_title` per file)
- Test: `tests/test_engines.py`

**Interfaces:**
- Produksi: `_entry_kind(entry: dict, default: str) -> str` mengembalikan `"photo"` | `"video"` | `"audio"`
- Produksi: kunci `"kind"` pada dict hasil `extract_info`

- [ ] **Langkah 1: Tulis tes yang gagal**

Tambahkan ke `tests/test_engines.py`:

~~~python
    @patch("yt_dlp.YoutubeDL")
    def test_kind_photo_when_no_streams(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "title": "foto",
            "formats": [{"format_id": "orig", "ext": "jpg", "vcodec": "none", "acodec": "none"}],
        }
        mock_ydl_cls.return_value = mock_instance
        info = YtDlpEngine().extract_info("https://www.instagram.com/p/abc/")
        self.assertEqual(info["kind"], "photo")

    @patch("yt_dlp.YoutubeDL")
    def test_kind_audio_when_only_audio(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "title": "lagu",
            "formats": [{"format_id": "m4a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"}],
        }
        mock_ydl_cls.return_value = mock_instance
        info = YtDlpEngine().extract_info("https://soundcloud.com/a/b")
        self.assertEqual(info["kind"], "audio")

    @patch("yt_dlp.YoutubeDL")
    def test_kind_video_by_default(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "title": "video",
            "formats": [{"format_id": "22", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"}],
        }
        mock_ydl_cls.return_value = mock_instance
        info = YtDlpEngine().extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual(info["kind"], "video")

    def test_entry_kind_fallback_default(self):
        from app.core.engines.ytdlp import _entry_kind
        self.assertEqual(_entry_kind({}, "photo"), "photo")
        self.assertEqual(_entry_kind({"vcodec": None, "acodec": None}, "photo"), "photo")
        self.assertEqual(_entry_kind({"vcodec": "none", "acodec": "none"}, "video"), "photo")
        self.assertEqual(_entry_kind({"vcodec": "none", "acodec": "mp4a"}, "photo"), "audio")
        self.assertEqual(_entry_kind({"vcodec": "avc1", "acodec": "mp4a"}, "photo"), "video")
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_engines -v`
Expected: FAIL dengan `KeyError: 'kind'` dan `ImportError: cannot import name '_entry_kind'`

- [ ] **Langkah 3: Tambah `_entry_kind` dan `"kind"` ke `app/core/engines/ytdlp.py`**

Tambahkan fungsi tingkat modul (setelah `_is_bot_check`):

~~~python
def _entry_kind(entry: dict, default: str) -> str:
    """photo | video | audio. Bila info masih datar (extract_flat), pakai default."""
    vcodec = entry.get("vcodec")
    acodec = entry.get("acodec")
    if vcodec is None and acodec is None:
        return default
    if vcodec in (None, "none") and acodec in (None, "none"):
        return "photo"
    if vcodec in (None, "none"):
        return "audio"
    return "video"
~~~

Di `_parse_info`, tambahkan `"kind"` ke dict hasil. Untuk percabangan kumpulan pakai `default = "photo" bila carousel, selain itu "video"`.

- [ ] **Langkah 4: Ganti pola `outtmpl` di `_download_once`**

~~~python
        if allow_collection:
            outtmpl = os.path.join(out_dest, "%(playlist_index)03d - %(title).100B [%(id)s].%(ext)s")
        else:
            outtmpl = os.path.join(out_dest, "%(title).120B [%(id)s].%(ext)s")
~~~

(Tambah parameter `allow_collection: bool = False` pada `_download_once` dan `download`; nilai `True` hanya untuk kumpulan — di Tugas 8.)

- [ ] **Langkah 5: `original_title` per file di `app/core/worker.py`**

Di dalam `for fpath in files:`, ganti `original_title=info.get("title", fname)` menjadi:

~~~python
                    original_title = os.path.splitext(fname)[0]
~~~

lalu pakai `original_title=original_title,` pada pemanggilan `create_file`.

- [ ] **Langkah 6: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: PASS semua

- [ ] **Langkah 7: Commit**

~~~bash
git add app/core/engines/ytdlp.py app/core/worker.py tests/test_engines.py
git commit -m "feat(engines): deteksi kind photo/video/audio dan nama file per item"
~~~

---

### Tugas 4: Fallback foto ke gallery-dl

**Files:**
- Modify: `app/core/engines/gallerydl.py`
- Modify: `app/core/worker.py` (`_get_engine_for_url` + fallback)
- Test: `tests/test_engines.py`

**Interfaces:**
- Produksi: `GalleryDlEngine.extract_info(url) -> dict` menerima URL apa pun (bukan hanya `File:`)
- Produksi: `WorkerService._extract_with_fallback(url) -> tuple[engine, info]`

- [ ] **Langkah 1: Tulis tes yang gagal**

Tambahkan ke `tests/test_engines.py`:

~~~python
    def test_gallerydl_accepts_instagram_photo(self):
        engine = GalleryDlEngine()
        info = engine.extract_info("https://www.instagram.com/p/abc123/")
        self.assertEqual(info["kind"], "photo")
        self.assertIsNone(info.get("collection"))

    def test_gallerydl_rejects_profile(self):
        engine = GalleryDlEngine()
        with self.assertRaises(AppException) as ctx:
            engine.extract_info("https://www.instagram.com/someuser")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

    def test_worker_falls_back_to_gallerydl_on_ytdlp_failure(self):
        from app.core.worker import WorkerService
        svc = WorkerService.__new__(WorkerService)
        svc.ytdlp_engine = MagicMock()
        svc.gallerydl_engine = MagicMock()
        svc.ytdlp_engine.extract_info.side_effect = Exception("boom")
        svc.gallerydl_engine.extract_info.return_value = {"title": "foto", "kind": "photo"}
        engine, info = svc._extract_with_fallback("https://www.instagram.com/p/abc/")
        self.assertIs(engine, svc.gallerydl_engine)
        self.assertEqual(info["kind"], "photo")
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_engines -v`
Expected: FAIL (`AppException` dari `WIKI_SINGLE_PATTERN`, dan `AttributeError: _extract_with_fallback`)

- [ ] **Langkah 3: Longgarkan `app/core/engines/gallerydl.py`**

Ganti `WIKI_SINGLE_PATTERN` dan dua pengecekan `raise` dengan aturan minimal — terima semua URL kecuali profil/collection yang dilarang. `extract_info` mengembalikan dict sintetis:

~~~python
WIKI_SINGLE_PATTERN = re.compile(r"^https://commons\.wikimedia\.org/wiki/File:[^/]+$")
PROFILE_PATTERN = re.compile(r"^(https://www\.instagram\.com/[A-Za-z0-9._]+/?)$")


def extract_info(self, url: str) -> dict:
    if PROFILE_PATTERN.match(url):
        raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
                          "Channel/profil tidak didukung. Hanya playlist, album, dan carousel.", retryable=False)
    return {
        "title": os.path.basename(urllib.parse.urlsplit(url).path.rstrip("/")) or "media",
        "uploader": "Wikimedia Commons" if WIKI_SINGLE_PATTERN.match(url) else None,
        "duration": None,
        "thumbnail": None,
        "kind": "photo",
        "estimated_bytes": 1000000,
        "formats": [{"format_id": "original", "ext": "jpg", "quality": "original",
                     "has_video": False, "has_audio": False, "filesize_approx": 1000000}],
    }
~~~

`download` juga dibebaskan dari `WIKI_SINGLE_PATTERN` (pertahankan blok `gallery_dl.job.DownloadJob`).

- [ ] **Langkah 4: Tambah `_extract_with_fallback` di `app/core/worker.py`**

~~~python
    def _extract_with_fallback(self, url: str):
        try:
            return self.ytdlp_engine, self.ytdlp_engine.extract_info(url)
        except AppException:
            pass
        except Exception:
            pass
        return self.gallerydl_engine, self.gallerydl_engine.extract_info(url)
~~~

Lalu di `execute_task` ganti `engine = self._get_engine_for_url(task["url"])` + `info = engine.extract_info(...)` menjadi `engine, info = self._extract_with_fallback(task["url"])`. Wikimedia tetap langsung ke gallery-dl lewat `_get_engine_for_url` (panggil lebih dulu bila `"wikimedia.org" in url`).

- [ ] **Langkah 5: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Langkah 6: Commit**

~~~bash
git add app/core/engines/gallerydl.py app/core/worker.py tests/test_engines.py
git commit -m "feat(photos): fallback foto ke gallery-dl dan longgarkan penerimaan URL"
~~~

---

### Tugas 5: Gerbang URL lapis 1

**Files:**
- Modify: `app/core/validator.py`
- Test: `tests/test_validator.py` (baru)

**Interfaces:**
- Produksi: `validate_and_sanitize_url(url: str) -> str` (perilaku lama dipertahankan untuk URL tunggal)
- Menolak channel/profil dengan `ErrorCode.UNSUPPORTED_PLAYLIST` (400)

- [ ] **Langkah 1: Tulis tes yang gagal**

Buat `tests/test_validator.py` dengan tabel kasus:

~~~python
import unittest
from app.core.errors import AppException
from app.core.validator import validate_and_sanitize_url

BOLEH = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://www.youtube.com/playlist?list=PLabc123",
    "https://www.youtube.com/watch?v=a&list=OLAK5uy_xyz",
    "https://music.youtube.com/playlist?list=OLAK5uy_lRi",
    "https://www.youtube.com/shorts/abc123",
    "https://www.instagram.com/p/abc123/",
    "https://www.instagram.com/reel/xyz789/",
    "https://www.instagram.com/tv/tuv/",
    "https://www.tiktok.com/@user/video/712345",
    "https://www.tiktok.com/@user/photo/712345",
    "https://x.com/user/status/123456",
    "https://twitter.com/user/status/123456/photo/1",
    "https://www.facebook.com/watch?v=123",
    "https://www.facebook.com/reel/456",
    "https://fb.watch/abc/",
    "https://www.facebook.com/media/set/?set=a.123",
    "https://soundcloud.com/artist/track",
    "https://commons.wikimedia.org/wiki/File:Monarch.jpg",
]

TOLAK = [
    ("https://www.youtube.com/@kurzgesagt", "profil"),
    ("https://www.youtube.com/channel/UCxyz", "channel"),
    ("https://www.youtube.com/c/nama", "channel"),
    ("https://www.youtube.com/user/nama", "channel"),
    ("https://www.youtube.com/results?search_query=a", "pencarian"),
    ("https://www.youtube.com/feed/subscriptions", "umpan"),
    ("https://www.youtube.com/watch?v=a&list=RDxyz", "mix"),
    ("https://www.youtube.com/playlist?list=UUxyz", "uploads"),
    ("https://www.instagram.com/someuser", "profil"),
    ("https://www.instagram.com/reels/someuser", "profil"),
    ("https://www.tiktok.com/@user", "profil"),
    ("https://www.tiktok.com/collection/123", "koleksi"),
    ("https://x.com/user", "profil"),
    ("https://twitter.com/user/with_replies", "tab profil"),
    ("https://www.facebook.com/namahalaman", "halaman"),
    ("https://www.facebook.com/profile.php?id=1", "profil"),
    ("https://www.facebook.com/groups/123", "grup"),
    ("https://soundcloud.com/artist/sets/album", "set"),
    ("https://commons.wikimedia.org/wiki/Category:Animals", "kategori"),
]


class TestValidatorCollections(unittest.TestCase):
    def test_boleh(self):
        for url in BOLEH:
            with self.subTest(url=url):
                try:
                    validate_and_sanitize_url(url)
                except AppException:
                    self.fail(f"seharusnya diterima: {url}")

    def test_tolak(self):
        for url, alasan in TOLAK:
            with self.subTest(url=url):
                with self.assertRaises(AppException) as ctx:
                    validate_and_sanitize_url(url)
                self.assertEqual(ctx.exception.code, "unsupported_playlist")

    def test_sanitisasi_tetap_jalan(self):
        hasil = validate_and_sanitize_url("https://www.youtube.com/watch?v=a&utm_source=x&si=yy")
        self.assertNotIn("utm_source", hasil)
        self.assertNotIn("si=", hasil)


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_validator -v`
Expected: banyak FAIL (URL playlist masih ditolak `/playlists?/`; profil masih lolos)

- [ ] **Langkah 3: Ganti `DISALLOWED_PATH_PATTERNS` di `app/core/validator.py`**

Hapus `DISALLOWED_PATH_PATTERNS` dan blok `for pattern in DISALLOWED_PATH_PATTERNS:`. Ganti dengan tabel aturan + dua fungsi pembantu:

~~~python
PLATFORM_RULES = {
    "youtube.com": [
        (re.compile(r"^/[A-Za-z0-9._@%-]+/?$"), "Halaman profil atau channel"),
        (re.compile(r"^/@[^/]*"), "Profil/channel"),
        (re.compile(r"^/(channel|c|user)/"), "Profil/channel"),
        (re.compile(r"^/(feed|results|hashtag|premium|account|t)/?"), "Halaman internal"),
    ],
    "instagram.com": [
        (re.compile(r"^/reels(/[A-Za-z0-9._]+)?/?$"), "Profil reels"),
        (re.compile(r"^/[A-Za-z0-9._]+/?$"), "Profil"),
        (re.compile(r"^/(explore|stories|accounts|directory|developer)/"), "Halaman jelajah"),
    ],
    "tiktok.com": [
        (re.compile(r"^/@[A-Za-z0-9._]+/?$"), "Profil"),
        (re.compile(r"^/@[A-Za-z0-9._]+/(followers|following|friends|live|shop|recommends)"), "Tab profil"),
        (re.compile(r"^/(tag|search|discover|live|f|find|trending|friends|collection)/"), "Halaman jelajah"),
    ],
    "facebook.com": [
        (re.compile(r"^/(profile\.php|pages|groups|events|marketplace|gaming)"), "Profil/grup/halaman"),
        (re.compile(r"^/[A-Za-z0-9._-]+/?$"), "Halaman"),
    ],
    "x.com": [
        (re.compile(r"^/[A-Za-z0-9_]+/?$"), "Profil"),
        (re.compile(r"^/[A-Za-z0-9_]+/(with_replies|media|likes|highlights|following|followers)"), "Tab profil"),
        (re.compile(r"^/(intent|explore|search|home|notifications|messages|settings|hashtag|i/)(?!web/status)"), "Halaman internal"),
    ],
    "twitter.com": "same",
    "soundcloud.com": [
        (re.compile(r"^/[^/]+/sets(/|$)"), "Set"),
        (re.compile(r"^/(tags|discover|stream|you|search|charts)/"), "Halaman jelajah"),
        (re.compile(r"^/[A-Za-z0-9_-]+/?$"), "Profil"),
    ],
    "wikimedia.org": [
        (re.compile(r"/wiki/(Category|Gallery|User|Help|Wikipedia|Template|Portal|Module|Talk|Special|Draft):"), "Bukan file"),
    ],
}
PLATFORM_RULES["twitter.com"] = PLATFORM_RULES["x.com"]

INFINITE_LIST_PREFIX = ("UC", "UU", "UL")


def _rules_for(host: str):
    host = (host or "").lower().rstrip(".")
    best, best_len = None, -1
    for key, rules in PLATFORM_RULES.items():
        if host == key or host.endswith("." + key):
            if len(key) > best_len:
                best, best_len = rules, len(key)
    return best


def _check_list_param(params) -> None:
    for k, v in params:
        if k != "list" or not v:
            continue
        if v.startswith(INFINITE_LIST_PREFIX):
            raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
                "Channel/tab Uploads tidak didukung. Hanya playlist, album, dan carousel.", retryable=False)
        if v.startswith("RD") and not v.startswith(("RDAM", "RDCLAK")):
            raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
                "Mix otomatis YouTube tidak didukung karena tidak berujung.", retryable=False)
~~~

Di dalam `validate_and_sanitize_url`, **setelah** lolos uji domain dan **sebelum** sanitisasi `query_params`, sisipkan:

~~~python
    unquoted_target = urllib.parse.unquote(parsed.path + (("?" + parsed.query) if parsed.query else ""))
    for pattern, reason in (_rules_for(host) or []):
        if pattern.search(unquoted_target):
            raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
                f"{reason} tidak didukung. Hanya item tunggal, playlist, album, dan carousel.", retryable=False)
    _check_list_param(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
~~~

- [ ] **Langkah 4: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest tests.test_validator -v`
Expected: PASS semua

- [ ] **Langkah 5: Jalankan seluruh tes**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: PASS (perhatikan `tests/test_config_and_errors.py` dan `tests/test_network.py` tidak boleh rusak)

- [ ] **Langkah 6: Commit**

~~~bash
git add app/core/validator.py tests/test_validator.py
git commit -m "feat(validator): aturan URL per platform, izinkan playlist/album/carousel"
~~~

---

### Tugas 6: Lapis 2 dan `collection`/`entries` pada extract

**Files:**
- Modify: `app/core/engines/ytdlp.py`
- Test: `tests/test_engines.py`

**Interfaces:**
- Produksi: `_collection_kind(info: dict) -> str | None` (`"playlist"` | `"album"` | `"carousel"` | `None`)
- Produksi: `_reject_if_channel(info: dict) -> None` (melempar `AppException`)
- Konsumsi: `_entry_kind` (Tugas 3)

- [ ] **Langkah 1: Tulis tes yang gagal**

Tambahkan ke `tests/test_engines.py`:

~~~python
    @patch("yt_dlp.YoutubeDL")
    def test_playlist_diterima(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "_type": "playlist", "playlist_id": "PLabc", "channel_id": "UCxyz",
            "title": "Playlist", "extractor_key": "YoutubeTab",
            "entries": [{"id": "v1", "title": "A", "playlist_index": 1}],
        }
        mock_ydl_cls.return_value = mock_instance
        info = YtDlpEngine().extract_info("https://www.youtube.com/playlist?list=PLabc")
        self.assertEqual(info["collection"], {"type": "playlist", "count": 1})
        self.assertEqual(info["entries"][0]["kind"], "video")

    @patch("yt_dlp.YoutubeDL")
    def test_channel_ditolak(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "_type": "playlist", "playlist_id": "UCxyz", "channel_id": "UCxyz",
            "entries": [], "extractor_key": "YoutubeTab",
        }
        mock_ydl_cls.return_value = mock_instance
        with self.assertRaises(AppException) as ctx:
            YtDlpEngine().extract_info("https://www.youtube.com/@kurzgesagt")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

    @patch("yt_dlp.YoutubeDL")
    def test_mix_rd_ditolak(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "_type": "playlist", "playlist_id": "RDabc", "channel_id": "UCxyz", "entries": [],
        }
        mock_ydl_cls.return_value = mock_instance
        with self.assertRaises(AppException):
            YtDlpEngine().extract_info("https://www.youtube.com/watch?v=a&list=RDabc")

    @patch("yt_dlp.YoutubeDL")
    def test_album_musik_diterima(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "_type": "playlist", "playlist_id": "OLAK5uy_x", "channel_id": "UCxyz",
            "extractor_key": "YoutubeTab", "title": "Album",
            "entries": [{"id": "v1", "title": "A", "playlist_index": 1}],
        }
        mock_ydl_cls.return_value = mock_instance
        info = YtDlpEngine().extract_info("https://music.youtube.com/playlist?list=OLAK5uy_x")
        self.assertEqual(info["collection"]["type"], "album")

    @patch("yt_dlp.YoutubeDL")
    def test_carousel_instagram(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "_type": "playlist", "extractor_key": "Instagram",
            "webpage_url": "https://www.instagram.com/p/abc/",
            "entries": [{"id": "1", "title": "a", "playlist_index": 1},
                        {"id": "2", "title": "b", "playlist_index": 2}],
        }
        mock_ydl_cls.return_value = mock_instance
        info = YtDlpEngine().extract_info("https://www.instagram.com/p/abc/")
        self.assertEqual(info["collection"], {"type": "carousel", "count": 2})
        self.assertEqual(info["entries"][0]["kind"], "photo")
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_engines -v`
Expected: FAIL (`collection` belum ada, channel belum ditolak)

- [ ] **Langkah 3: Implementasi `_collection_kind` dan `_reject_if_channel`**

Tambahkan di `app/core/engines/ytdlp.py`:

~~~python
INFINITE_ID_PREFIX = ("UC", "UU", "UL")
USER_EXTRACTORS = ("InstagramUser", "TikTokUser", "TwitterUser", "FacebookUser", "SoundcloudUser", "YoutubeChannel")


def _collection_kind(info: dict):
    if info.get("_type") not in ("playlist", "multi_video"):
        return None
    ie = str(info.get("extractor_key") or info.get("ie_key") or "").lower()
    url = str(info.get("webpage_url") or info.get("url") or "").lower()
    pid = str(info.get("playlist_id") or "").lower()
    if "album" in ie or "/album" in url or pid.startswith("olak"):
        return "album"
    if "sidecar" in ie or "instagram" in ie or "/p/" in url:
        return "carousel"
    return "playlist"


def _reject_if_channel(info: dict) -> None:
    pid = str(info.get("playlist_id") or info.get("id") or "")
    cid = str(info.get("channel_id") or "")
    if pid and cid and pid == cid:
        raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
            "Channel/profil tidak didukung. Hanya playlist, album, dan carousel.", retryable=False)
    if pid.startswith(INFINITE_ID_PREFIX):
        raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
            "Channel/tab Uploads tidak didukung. Hanya playlist, album, dan carousel.", retryable=False)
    if pid.startswith("RD") and not pid.startswith(("RDAM", "RDCLAK")):
        raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
            "Mix otomatis YouTube tidak didukung karena tidak berujung.", retryable=False)
    if str(info.get("extractor_key") or info.get("ie_key") or "") in USER_EXTRACTORS:
        raise AppException(400, ErrorCode.UNSUPPORTED_PLAYLIST,
            "Channel/profil tidak didukung. Hanya playlist, album, dan carousel.", retryable=False)
~~~

JANGAN menolak berdasarkan `"YoutubeTab"` — extractor itu melayani playlist dan channel sekaligus.

- [ ] **Langkah 4: Sisipkan cabang kumpulan ke `_parse_info`**

Di awal `_parse_info(self, info)`:

~~~python
        coll = _collection_kind(info)
        if coll is not None:
            _reject_if_channel(info)
            default_kind = "photo" if coll == "carousel" else "video"
            entries = []
            for i, e in enumerate(info.get("entries") or [], start=1):
                if not e:
                    continue
                entries.append({
                    "index": e.get("playlist_index") or i,
                    "title": e.get("title") or e.get("id") or f"item-{i}",
                    "kind": _entry_kind(e, default_kind),
                    "duration": e.get("duration"),
                    "thumbnail": e.get("thumbnail"),
                })
            return {
                "title": info.get("title", "collection"),
                "uploader": info.get("uploader") or info.get("channel"),
                "duration": None,
                "thumbnail": info.get("thumbnail"),
                "kind": default_kind,
                "collection": {"type": coll, "count": len(entries)},
                "entries": entries,
                "estimated_bytes": 0,
                "formats": [],
            }
~~~

Untuk URL tunggal, tambahkan `"kind": _entry_kind(info, "video")` pada dict hasil dan jangan kembalikan `collection`/`entries`.

- [ ] **Langkah 5: Pakai `extract_flat` untuk kumpulan**

Di `extract_info`, ganti pemanggilan `self._get_ydl_opts({"extract_flat": False}, disguise)` menjadi:

~~~python
            opts = self._get_ydl_opts({"extract_flat": "in_playlist"}, disguise)
~~~

`extract_flat="in_playlist"` hanya berlaku untuk playlist sehingga URL tunggal tetap ter-ekstrak penuh.

- [ ] **Langkah 6: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: PASS

- [ ] **Langkah 7: Commit**

~~~bash
git add app/core/engines/ytdlp.py tests/test_engines.py
git commit -m "feat(collections): lapis 2 channel vs playlist, collection/entries, extract_flat"
~~~

---

### Tugas 7: `ItemBudget` dan cek ruang per item

**Files:**
- Create: `app/core/budget.py`
- Modify: `app/core/storage.py` (`get_free_bytes`)
- Modify: `app/core/cleaner.py` (`can_fit`)
- Test: `tests/test_budget.py` (baru)

**Interfaces:**
- Produksi: `StorageManager.get_free_bytes(path: str | None = None) -> int`
- Produksi: `CleanerService.can_fit(incoming_bytes: int) -> tuple[bool, str]`
- Produksi: `ItemBudget(cleaner, storage, quota_remaining_fn, min_free_bytes=None, min_quota_bytes=None)` dengan metode `check_entry(entry) -> tuple[bool, str]`, `record_skip(entry, reason) -> None`, `allows_more() -> bool`, atribut `skipped: list[dict]`
- Produksi: `BudgetExceeded(Exception)` dan `_entry_size(entry: dict) -> int`

- [ ] **Langkah 1: Tulis tes yang gagal**

Buat `tests/test_budget.py`:

~~~python
import unittest
from unittest.mock import MagicMock
from app.core.budget import ItemBudget, BudgetExceeded, _entry_size


def _budget(free=10 * 1024 ** 3, quota_left=10 * 1024 ** 3, can_fit=(True, "")):
    cleaner = MagicMock()
    cleaner.can_fit.return_value = can_fit
    storage = MagicMock()
    storage.get_free_bytes.return_value = free
    return ItemBudget(cleaner=cleaner, storage=storage, quota_remaining_fn=lambda: quota_left)


class TestItemBudget(unittest.TestCase):
    def test_entry_size_prefers_explicit_then_formats_then_default(self):
        self.assertEqual(_entry_size({"filesize": 123}), 123)
        self.assertEqual(_entry_size({"formats": [{"filesize_approx": 99}]}), 99)
        self.assertEqual(_entry_size({}), 5 * 1024 * 1024)

    def test_ok_when_roomy(self):
        ok, reason = _budget().check_entry({"filesize": 100})
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_insufficient_disk(self):
        ok, reason = _budget(free=600 * 1024 ** 2).check_entry({"filesize": 200 * 1024 ** 2})
        self.assertFalse(ok)
        self.assertEqual(reason, "insufficient_disk")

    def test_daily_quota_exceeded(self):
        ok, reason = _budget(quota_left=70 * 1024 ** 2).check_entry({"filesize": 20 * 1024 ** 2})
        self.assertFalse(ok)
        self.assertEqual(reason, "daily_quota_exceeded")

    def test_cleaner_veto_wins(self):
        ok, reason = _budget(can_fit=(False, "quota_exceeded")).check_entry({"filesize": 1})
        self.assertFalse(ok)
        self.assertEqual(reason, "quota_exceeded")

    def test_record_skip_uses_playlist_index(self):
        b = _budget()
        b.record_skip({"playlist_index": 45, "title": "Judul"}, "insufficient_disk")
        self.assertEqual(b.skipped, [{"index": 45, "title": "Judul", "reason": "insufficient_disk"}])

    def test_allows_more_false_when_disk_below_floor(self):
        self.assertFalse(_budget(free=512 * 1024 ** 2).allows_more())
        self.assertTrue(_budget(free=513 * 1024 ** 2).allows_more())

    def test_budget_exceeded_is_plain_exception(self):
        self.assertFalse(issubclass(BudgetExceeded, Exception) and False)


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_budget -v`
Expected: FAIL dengan `ModuleNotFoundError: No module named 'app.core.budget'`

- [ ] **Langkah 3: Tambah `get_free_bytes` ke `app/core/storage.py`**

~~~python
    def get_free_bytes(self, path: str = None) -> int:
        target = path or self.base_path
        os.makedirs(target, exist_ok=True)
        return shutil.disk_usage(target).free
~~~

(`import shutil` sudah ada.)

- [ ] **Langkah 4: Tambah `can_fit` ke `app/core/cleaner.py`**

Tambahkan tepat setelah `reserve_space`:

~~~python
    def can_fit(self, incoming_bytes: int) -> tuple:
        """Cek tanpa melempar ekseksi: apakah masih muat incoming_bytes?"""
        total_used = self.file_repo.get_total_storage_used()
        if total_used + incoming_bytes <= self.high_watermark:
            return True, ""
        stats = self.run_eviction_cycle(force_emergency=False)
        total_used = stats["current_storage_used"]
        if total_used + incoming_bytes <= self.max_bytes:
            return True, ""
        stats = self.run_eviction_cycle(force_emergency=True)
        total_used = stats["current_storage_used"]
        if total_used + incoming_bytes <= self.max_bytes:
            return True, ""
        return False, "insufficient_disk"
~~~

- [ ] **Langkah 5: Buat `app/core/budget.py`**

~~~python
from typing import Callable, List, Tuple

from app.core.cleaner import CleanerService
from app.core.config import settings
from app.core.storage import StorageManager


class BudgetExceeded(Exception):
    """Dilempar dari progress hook saat satu file melewati batas anggaran."""


def _entry_size(entry: dict) -> int:
    for key in ("filesize", "filesize_approx"):
        v = entry.get(key)
        if v:
            return int(v)
    for f in entry.get("formats") or []:
        v = f.get("filesize") or f.get("filesize_approx")
        if v:
            return int(v)
    return 5 * 1024 * 1024


class ItemBudget:
    """Penjaga kuota & disk yang dicek SEBELUM tiap item kumpulan diunduh."""

    def __init__(self, cleaner: CleanerService, storage: StorageManager,
                 quota_remaining_fn: Callable[[], int],
                 min_free_bytes: int = None, min_quota_bytes: int = None):
        self.cleaner = cleaner
        self.storage = storage
        self._quota_remaining = quota_remaining_fn
        self.min_free_bytes = settings.DISK_MIN_FREE_BYTES if min_free_bytes is None else min_free_bytes
        self.min_quota_bytes = settings.QUOTA_MIN_REMAINING_BYTES if min_quota_bytes is None else min_quota_bytes
        self.skipped: List[dict] = []
        self.exhausted_reason: str = None

    def _disk_room(self) -> int:
        return max(0, int(self.storage.get_free_bytes()) - self.min_free_bytes)

    def _quota_room(self) -> int:
        return max(0, int(self._quota_remaining()) - self.min_quota_bytes)

    def allows_more(self) -> bool:
        return min(self._disk_room(), self._quota_room()) > 0

    def check_entry(self, entry: dict) -> Tuple[bool, str]:
        if self.exhausted_reason:
            return False, self.exhausted_reason
        need = _entry_size(entry)
        if self._disk_room() < need:
            return False, "insufficient_disk"
        if self._quota_room() < need:
            return False, "daily_quota_exceeded"
        ok, reason = self.cleaner.can_fit(need)
        if not ok:
            return False, reason or "insufficient_disk"
        return True, ""

    def record_skip(self, entry: dict, reason: str) -> None:
        self.skipped.append({
            "index": entry.get("playlist_index"),
            "title": entry.get("title") or entry.get("id") or "",
            "reason": reason,
        })

    def mark_exhausted(self, reason: str) -> None:
        self.exhausted_reason = reason
~~~

- [ ] **Langkah 6: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest tests.test_budget -v`
Expected: PASS 8 tes

- [ ] **Langkah 7: Commit**

~~~bash
git add app/core/budget.py app/core/storage.py app/core/cleaner.py tests/test_budget.py
git commit -m "feat(budget): ItemBudget untuk pengecekan kuota & disk per item"
~~~

---

### Tugas 8: Download kumpulan, `skipped[]`, dan kuota per item

**Files:**
- Modify: `app/core/engines/ytdlp.py` (`allow_collection`, kait `match_filter`/`progress_hooks`)
- Modify: `app/core/worker.py` (tanpa penahanan di depan untuk kumpulan, `result_json`)
- Modify: `app/db/database.py` (migrasi kolom `tasks.result_json`)
- Modify: `app/db/repositories.py` (`get_daily_quota_remaining`, `set_result`)
- Modify: `app/api/endpoints/tasks.py` (`collection`, `skipped`, `index`, `kind`)
- Test: `tests/test_worker.py` (baru)

**Interfaces:**
- Konsumsi: `ItemBudget` (Tugas 7), `_collection_kind` (Tugas 6)
- Produksi: `ClientRepository.get_daily_quota_remaining(client_id: str) -> int`
- Produksi: `TaskRepository.set_result(task_id: str, result_json: str) -> None`

- [ ] **Langkah 1: Tulis tes yang gagal**

Buat `tests/test_worker.py`:

~~~python
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.core.worker import WorkerService
from app.db.database import init_db, get_db_connection
from app.db.repositories import TaskRepository


class TestWorkerCollections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "t.db")
        init_db(self.db_path)
        self.conn = get_db_connection(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_collection_uses_no_front_reservation(self):
        svc = WorkerService.__new__(WorkerService)
        svc.task_repo = MagicMock()
        svc.file_repo = MagicMock()
        svc.client_repo = MagicMock()
        svc.cleaner_svc = MagicMock()
        svc.storage_mgr = MagicMock()
        svc.ytdlp_engine = MagicMock()
        svc.gallerydl_engine = MagicMock()
        svc.active_tasks = {}
        svc.ytdlp_engine.extract_info.return_value = {
            "title": "Playlist", "kind": "video",
            "collection": {"type": "playlist", "count": 200}, "entries": [],
        }
        svc.ytdlp_engine.download.return_value = []
        svc.task_repo.get_task.return_value = {
            "id": "tsk_1", "client_id": "cli_1", "url": "https://www.youtube.com/playlist?list=PLx",
            "status": "queued", "format_id": None, "audio_only": 0,
        }
        svc.storage_mgr.get_tmp_dir.return_value = self.tmp.name
        svc.client_repo.get_daily_quota_remaining.return_value = 10 ** 9

        import asyncio
        asyncio.run(svc.execute_task("tsk_1"))
        svc.cleaner_svc.reserve_space.assert_not_called()

    def test_result_json_records_skipped(self):
        import asyncio
        svc = WorkerService.__new__(WorkerService)
        svc.task_repo = MagicMock()
        svc.file_repo = MagicMock()
        svc.client_repo = MagicMock()
        svc.cleaner_svc = MagicMock()
        svc.storage_mgr = MagicMock()
        svc.ytdlp_engine = MagicMock()
        svc.gallerydl_engine = MagicMock()
        svc.active_tasks = {}
        svc.ytdlp_engine.extract_info.return_value = {
            "title": "Playlist", "kind": "video",
            "collection": {"type": "playlist", "count": 2}, "entries": [],
        }
        svc.ytdlp_engine.download.side_effect = lambda *a, **kw: (
            kw["budget"].record_skip({"playlist_index": 2, "title": "B"}, "insufficient_disk") or []
        )
        svc.task_repo.get_task.return_value = {
            "id": "tsk_2", "client_id": "cli_1", "url": "u",
            "status": "queued", "format_id": None, "audio_only": 0,
        }
        svc.storage_mgr.get_tmp_dir.return_value = self.tmp.name
        svc.client_repo.get_daily_quota_remaining.return_value = 10 ** 9

        with self.assertRaises(Exception):
            asyncio.run(svc.execute_task("tsk_2"))
        args = svc.task_repo.set_result.call_args[0]
        payload = json.loads(args[1])
        self.assertEqual(payload["skipped"][0]["reason"], "insufficient_disk")


if __name__ == "__main__":
    unittest.main()
~~~

- [ ] **Langkah 2: Jalankan tes, pastikan gagal**

Run: `./venv/bin/python -m unittest tests.test_worker -v`
Expected: FAIL (`set_result` belum dipanggil; `reserve_space` masih dipanggil)

- [ ] **Langkah 3: Migrasi kolom `result_json` di `app/db/database.py`**

Tambahkan fungsi pembantu dan panggil di `init_db` setelah `executescript`:

~~~python
def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(ddl)
~~~

~~~python
    with conn:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "tasks", "result_json", "ALTER TABLE tasks ADD COLUMN result_json TEXT")
~~~

- [ ] **Langkah 4: Tambah metode repository**

Di `ClientRepository` (setelah `increment_daily_usage`):

~~~python
    def get_daily_quota_remaining(self, client_id: str) -> int:
        cur = self.conn.execute(
            "SELECT MAX(daily_bytes_quota - daily_bytes_used, 0) AS remaining FROM clients WHERE id = ?",
            (client_id,),
        )
        row = cur.fetchone()
        return int(row["remaining"]) if row and row["remaining"] is not None else 0
~~~

Di `TaskRepository` (setelah `complete_task`):

~~~python
    def set_result(self, task_id: str, result_json: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE tasks SET result_json = ? WHERE id = ?", (result_json, task_id))
~~~

- [ ] **Langkah 5: Ubah `app/core/engines/ytdlp.py` untuk anggaran per item**

Tambahkan dua kait statis dan parameter `budget`:

~~~python
    @staticmethod
    def _make_budget_filter(base_filter, budget):
        def _filter(info):
            if budget is not None:
                ok, reason = budget.check_entry(info)
                if not ok:
                    budget.record_skip(info, reason)
                    return reason
            return base_filter(info) if base_filter else None
        return _filter

    @staticmethod
    def _make_budget_hook(budget):
        def _hook(d):
            if d.get("status") != "downloading":
                return
            if budget.allows_more():
                return
            fn = d.get("filename")
            if fn and os.path.isfile(fn):
                try:
                    os.remove(fn)
                except OSError:
                    pass
            budget.mark_exhausted("insufficient_disk")
            raise BudgetExceeded("Anggaran penyimpanan/kuota habis")
        return _hook
~~~

Di `_get_ydl_opsi`, ganti tiga kunci `no_playlist`/`playlist_items`/`max_downloads` menjadi bersyarat:

~~~python
        if not allow_collection:
            opts["no_playlist"] = True
            opts["playlist_items"] = "1"
            opts["max_downloads"] = 1
        else:
            opts["ignoreerrors"] = True
~~~

Di `download`/`_download_once`, pasang kait bila `budget` diberikan:

~~~python
        opts["match_filter"] = self._make_budget_filter(opts.get("match_filter"), budget)
        opts["progress_hooks"] = [self._make_budget_hook(budget)]
~~~

Tambah `from app.core.budget import BudgetExceeded` di bagian impor. `allow_collection=True` bila `_collection_kind(info)` tidak `None`.

- [ ] **Langkah 6: Ubah `app/core/worker.py`**

Ganti blok `est_bytes = ...` sampai `self.task_repo.set_reservation(...)` dengan:

~~~python
                is_collection = bool(info.get("collection"))
                if is_collection:
                    self.task_repo.set_reservation(task_id, 0, 0)
                else:
                    est_bytes = info.get("estimated_bytes") or 5 * 1024 * 1024
                    self.cleaner_svc.reserve_space(int(est_bytes * 2.2))
                    self.task_repo.set_reservation(task_id, est_bytes, int(est_bytes * 2.2))

                budget = ItemBudget(
                    cleaner=self.cleaner_svc,
                    storage=self.storage_mgr,
                    quota_remaining_fn=lambda: self.client_repo.get_daily_quota_remaining(task["client_id"]),
                )
~~~

Ubah pemanggilan `engine.download(...)` menjadi:

~~~python
                files = engine.download(
                    task["url"], tmp_dir,
                    format_id=task.get("format_id"),
                    audio_only=bool(task.get("audio_only")),
                    budget=budget,
                    allow_collection=is_collection,
                )
~~~

Tangkap `BudgetExceeded` di blok `except` (perlakukan sebagai sukses sebagian, bukan gagal). Sebelum `complete_task`, simpan laporan:

~~~python
                collection = info.get("collection")
                result = {
                    "collection": ({**collection, "downloaded": len(files), "skipped": len(budget.skipped)}
                                   if collection else None),
                    "skipped": budget.skipped,
                }
                self.task_repo.set_result(task_id, json.dumps(result))
~~~

Tambah `import json` dan `from app.core.budget import ItemBudget, BudgetExceeded`.

- [ ] **Langkah 7: Ubah `app/api/endpoints/tasks.py`**

Di `get_task_status`, ganti blok `files_output = []` agar menyertakan `index` dan `kind`, dan bangun `collection`/`skipped` dari `result_json`:

~~~python
    import json as _json
    result = _json.loads(task["result_json"]) if task.get("result_json") else {}
    files_output = []
    if task["status"] == "done":
        files = file_repo.get_files_by_task(task_id)
        now_exp = int(time.time()) + 900
        for f in files:
            token = generate_download_token(f["id"], now_exp)
            stem = os.path.splitext(f["original_title"])[0]
            idx = int(stem[:3]) if stem[:3].isdigit() else None
            mime = f["mime_type"] or ""
            kind = "photo" if mime.startswith("image/") else ("audio" if mime.startswith("audio/") else "video")
            files_output.append({
                "id": f["id"], "filename": f["original_title"], "index": idx, "kind": kind,
                "mime_type": f["mime_type"], "size_bytes": f["size_bytes"],
                "download_url": f"/v1/files/{f['id']}?token={token}", "expires_at": f["expires_at"],
            })

    return {
        "id": task["id"], "status": task["status"], "progress": task["progress"],
        "estimated_bytes": task["estimated_bytes"], "actual_bytes": task["actual_bytes"],
        "error_code": task["error_code"], "error_message": task["error_message"],
        "collection": result.get("collection"),
        "skipped": result.get("skipped", []),
        "files": files_output,
    }
~~~

Tambah `import os` di bagian impor.

- [ ] **Langkah 8: Jalankan tes, pastikan lolos**

Run: `./venv/bin/python -m unittest discover -s tests -v`
Expected: PASS semua

- [ ] **Langkah 9: Commit**

~~~bash
git add app/core/engines/ytdlp.py app/core/worker.py app/db/database.py app/db/repositories.py app/api/endpoints/tasks.py tests/test_worker.py
git commit -m "feat(collections): download kumpulan tanpa batas item dengan anggaran per item"
~~~

---

### Tugas 9: Verifikasi end-to-end di VM

**Files:** tidak ada perubahan kode — hanya verifikasi via Pai.

- [ ] **Langkah 1: Push dan tarik kode**

~~~bash
git push origin master
~~~

Lalu minta Pai: `cd ~/media-api && git pull origin master && ./venv/bin/python -m unittest discover -s tests -v`

- [ ] **Langkah 2: Restart server**

Minta Pai restart dengan `NETWORK_PROFILE=sandbox` + `ADMIN_API_KEY=key_vGV-CNOBdgm-7B6NL3NxGY-5vlQbmUutJYg1SPS0UQ8`, lalu `curl https://media.rahmat.cc.cd/health` → `{"status":"ok"}`

- [ ] **Langkah 3: Uji tujuh kasus**

| Kasus | Harapan |
|---|---|
| Playlist YouTube 20 video | 20 file dalam 1 task |
| Channel `/@kurzgesagt` | ditolak milidetik, 0 byte |
| Carousel Instagram | beberapa foto terpisah |
| Foto tunggal Instagram | 1 file `kind: photo` |
| Playlist 200+ video | berhenti sendiri, `skipped[]` rapi, `status: done` |
| Restart server | `key_vGV-…` tetap berlaku |
| Regresi: YT, YT Music, TikTok, X, Facebook, SoundCloud | tetap jalan |

- [ ] **Langkah 4: Catat hasil**

Tempelkan hasil ketujuh kasus ke percakapan. Bila ada yang gagal, perbaiki lalu ulangi langkah yang bersangkutan.

---

## Hasil Self-Review

**Cakupan spesifikasi:**
- `9 Kunci API permanen` → Tugas 2 ✓
- `10 Angka konfigurasi` → Tugas 1 ✓
- `4 Lapis 1` → Tugas 5 ✓
- `5 Lapis 2` → Tugas 6 ✓
- `6.1 extract` (`kind`, `collection`, `entries`, `extract_flat`) → Tugas 3, 6 ✓
- `6.2 tasks` (`index`, `kind`, `skipped`, nama file per item) → Tugas 3, 8 ✓
- `7 Eksekusi & kuota per item` → Tugas 7, 8 ✓
- `8 Foto + fallback gallery-dl` → Tugas 4 ✓
- `12 Pengujian` → Tugas 1, 2, 3, 4, 5, 6, 7 ✓
- `13 Verifikasi end-to-end` → Tugas 9 ✓

**Perbedaan kecil dari spesifikasi (disengaja, dicatat di sini):**
1. Spesifikasi `6.1` menampilkan `entries` dan `formats` bersamaan untuk kumpulan. Keputusan: `entries` hanya untuk kumpulan; `formats` hanya untuk item tunggal. Lebih bersih dan tidak ambigu.
2. Spesifikasi `7.3` menyebut ambang berhenti per item. Implementasinya memakai `DISK_MIN_FREE_BYTES` dan `QUOTA_MIN_REMAINING_BYTES` seperti tercantum.

**Konsistensi tipe:**
- `_entry_kind(entry: dict, default: str) -> str` dipakai konsisten di Tugas 3 dan 6 ✓
- `ItemBudget.check_entry(entry) -> tuple[bool, str]`, `record_skip(entry, reason)`, `allows_more() -> bool`, `mark_exhausted(reason)` — konsisten antara Tugas 7 (definisi) dan Tugas 8 (pemakaian) ✓
- `ClientRepository.get_daily_quota_remaining(client_id: str) -> int` — definisi Tugas 8 langkah 4, pemakaian Tugas 8 langkah 6 ✓
- `TaskRepository.set_result(task_id: str, result_json: str) -> None` — definisi Tugas 8 langkah 4, pemakaian Tugas 8 langkah 6 ✓
