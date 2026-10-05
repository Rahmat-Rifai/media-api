# Spesifikasi Desain — Download Foto, Download Playlist, dan API Key Permanen

- **Tanggal:** 2026-10-05
- **Repo:** `Rahmat-Rifai/media-api`, branch `master`
- **Status:** desain disetujui secara lisan oleh pemilik; menunggu review dokumen ini sebelum masuk ke rencana implementasi

---

## 1. Latar belakang & tujuan

Media API saat ini berprinsip **satu URL = satu file media**. Ada tiga permintaan baru:

1. **Download foto** — postingan foto tunggal maupun album/carousel (satu posting = beberapa foto) di Instagram, Twitter/X, Facebook, TikTok.
2. **Download playlist** — YouTube playlist, YouTube Music album/playlist, Instagram carousel, Facebook album.
3. **API key permanen** — `key_vGV-CNOBdgm-7B6NL3NxGY-5vlQbmUutJYg1SPS0UQ8` harus terus berlaku selamanya.

Kendala lingkungan yang memengaruhi desain:

- VM sandbox hanya punya disk **7,5 GB** (sisa 6,9 GB pada saat penulisan).
- Semua trafik keluar harus lewat **tunnel Cloudflare Worker** (metode POST dibungkus jadi GET).
- Ada pemeriksaan bot YouTube yang probabilistik per IP; sudah ditangani lewat penyamaran klien (`YOUTUBE_DISGUISES`).

## 2. Keputusan yang sudah diambil

| # | Pertanyaan | Keputusan |
|---|---|---|
| 1 | Arti "permanen" | **Hanya kunci API.** `download_url` tetap 15 menit, file tetap auto-hapus 1 hari |
| 2 | Bentuk hasil kumpulan | **Banyak file dalam 1 task**; tiap file punya `download_url` sendiri |
| 3 | Batas jumlah item | **Tanpa batas**; berhenti sendiri mengikuti kuota harian & sisa disk |
| 4 | Cakupan kumpulan | **Playlist, album, carousel saja.** Channel/profil, thread Twitter, dan kategori Wikimedia **tetap ditolak** |
| 5 | Pendekatan pemisahan | **Dua lapis** — gerbang URL lalu verifikasi metadata |

## 3. Arsitektur: gerbang dua lapis

~~~
URL masuk
   │
   ├─ Lapis 1  app/core/validator.py
   │           Aturan bentuk URL per platform.
   │           Menolak channel/profil dalam hitungan milidetik,
   │           tanpa panggilan jaringan sama sekali.
   │
   ├─ Lapis 2  app/core/engines/ytdlp.py  (setelah ekstrak)
   │           Verifikasi metadata: playlist_id vs channel_id,
   │           uji awalan ID, dan nama extractor.
   │           Menolak sebelum satu byte pun diunduh.
   │
   └─ Eksekusi app/core/worker.py
               Download per item dengan pengecekan kuota & disk
               sebelum tiap item.
~~~

Mengapa dua lapis: karena jumlah item **tidak dibatasi**, channel yang nyasar bisa berisi ribuan video. Lapis 1 menghemat waktu, lapis 2 menjadi jaring pengaman bila bentuk URL platform berubah atau ada URL aneh yang lolos.

## 4. Lapis 1 — aturan URL per platform

Menggantikan daftar hitam `DISALLOWED_PATH_PATTERNS` di `app/core/validator.py` yang sekarang memblokir `/playlists?/` dan `/albums?/` secara membabi buta.

| Platform | ✅ Boleh | ❌ Tetap ditolak |
|---|---|---|
| YouTube / YT Music | `/playlist?list=…`, `watch?v=…&list=…` | `/@handle`, `/channel/UC…`, `/c/…`, `/user/…`, `/results`, `/feed/…`, `list=RD…` (mix) |
| Instagram | `/p/<kode>/`, `/reel/<kode>/`, `/tv/<kode>/` (termasuk carousel) | `/<username>`, `/reels/<username>` |
| TikTok | `/@user/video/<id>`, `/@user/photo/<id>` (foto carousel) | `/@user` (profil), `/collection/…` |
| Facebook | `/watch?v=…`, `/reel/<id>`, `fb.watch/…`, `/posts/…`, `/photos/a.<album>/…`, `/media/set/…` | `/<namahalaman>`, `/profile.php?id=…` |
| Twitter/X | `x.com/<user>/status/<id>` | `x.com/<user>` (profil) |
| SoundCloud | track biasa | `/sets/` |
| Wikimedia | file tunggal | kategori & galeri |

Catatan implementasi:

- Aturan ditulis sebagai tabel data (daftar pola boleh + daftar pola tolak per host), bukan if-else panjang, supaya mudah dites dan ditambah.
- Aturan tolak **diperiksa lebih dulu** daripada aturan boleh.
- Sanitisasi yang sudah ada (hapus parameter `utm_*`, `fbclid`, `ref`, `si`; larangan IP literal, port non-443, userinfo) **tetap berlaku tidak berubah**.

## 5. Lapis 2 — membedakan kumpulan dari channel

Setelah `extract_info` mengembalikan hasil, periksa tiga hal berikut. **Salah satu cocok = tolak.**

| Uji | Arti |
|---|---|
| `playlist_id == channel_id` | Itu halaman channel, bukan playlist |
| `playlist_id` diawali `UC`, `UU`, `UL` | Channel, tab Uploads, atau Uploads otomatis |
| `playlist_id` diawali `RD` tapi **bukan** `RDAM` / `RDCLAK` | Mix otomatis YouTube yang tak berujung |
| nama extractor khusus profil pengguna (`InstagramUser`, `TikTokUser`, `TwitterUser`, `FacebookUser`, `SoundcloudUser`) | Halaman profil |

Yang **lolos** (contoh): `PL…`, `OLAK5uy_…` (album YT Music), `RDAM…` (album), `RDCLAK…` (playlist YT Music), carousel Instagram, album Facebook.

Koreksi atas temuan awal di lapangan: `RD` bukan penanda playlist — `RD<video_id>` adalah *mix* otomatis yang isinya bisa tak terbatas, justru harus ditolak. `RDAM` dan `RDCLAK` aman karena itu album/playlist nyata.

> ⚠️ **Jangan sekali-kali menolak berdasarkan nama extractor `YoutubeTab`.** Extractor itu melayani playlist *dan* channel sekaligus — menolaknya berarti semua playlist YouTube ikut mati. Untuk YouTube, andalkan dua uji di atas (`playlist_id == channel_id` dan awalan ID).

Kode galat tetap `unsupported_playlist` (kontrak tidak berubah), tapi **pesan** dibuat eksplisit, misalnya: *"Channel/profil tidak didukung. Hanya playlist, album, dan carousel yang bisa diunduh."*

## 6. Kontrak API

### 6.1 `POST /v1/extract`

~~~json
{
  "title": "Liburan Bali",
  "uploader": "Rahmat",
  "duration": null,
  "thumbnail": "https://…",
  "kind": "photo",
  "collection": { "type": "carousel", "count": 6 },
  "entries": [
    { "index": 1, "title": "Pantai Kuta", "kind": "photo" },
    { "index": 2, "title": "Sunset",     "kind": "photo" }
  ],
  "estimated_bytes": 0,
  "formats": [ { "format_id": "…", "ext": "jpg", "has_video": false, "has_audio": false } ]
}
~~~

- `kind` — nilai: `"photo"` | `"video"` | `"audio"`. Ditentukan dari `vcodec`/`acodec`: keduanya `none` → `photo`; hanya `vcodec` yang `none` → `audio`; selain itu → `video`.
- `collection` — **hanya ada** untuk kumpulan. `type`: `"playlist"` | `"album"` | `"carousel"`. `count` = jumlah item sebenarnya.
- `entries` — daftar isinya. Untuk kumpulan dipakai **`extract_flat`** supaya yt-dlp cukup mengambil daftar judulnya saja dan **tidak** mengulik metadata tiap item satu per satu. Playlist 200 video selesai dalam hitungan detik, bukan menit.
- `formats` tetap ada seperti sekarang untuk satu file.
- Sifat **mundur kompatibel** (backward compatible): field lama tidak ada yang dihapus atau berubah arti. Field baru bersifat tambahan.

### 6.2 `GET /v1/tasks/{task_id}`

~~~json
{
  "id": "tsk_…",
  "status": "done",
  "progress": 100.0,
  "actual_bytes": 104857600,
  "collection": { "type": "playlist", "count": 200, "downloaded": 44, "skipped": 156 },
  "files": [
    {
      "id": "fl_…",
      "filename": "01 - Pembukaan.mp4",
      "index": 1,
      "kind": "video",
      "mime_type": "video/mp4",
      "size_bytes": 5242880,
      "download_url": "/v1/files/fl_…?token=…",
      "expires_at": "2026-10-06 12:00:00"
    }
  ],
  "skipped": [
    { "index": 45, "title": "…", "reason": "insufficient_disk" }
  ]
}
~~~

Perbaikan bug lama yang ikut dibereskan: sekarang **semua** file dalam satu task mendapat `original_title` yang sama (judul induk). Diubah menjadi **nama per file** dari judul tiap item.

## 7. Eksekusi download & kuota per item

### 7.1 Masalah
`app/core/worker.py` sekarang menahan ruang sekali di depan: `reserved = estimated_bytes × 2.2`. Untuk kumpulan dengan jumlah item belum diketahui, hal itu mustahil dilakukan.

### 7.2 Solusi: pengecekan sebelum tiap item
Dipakai dua kait (hook) yt-dlp:

1. **`match_filter`** — dipanggil sebelum tiap item diunduh. Di dalamnya dicek sisa disk dan sisa kuota harian. Cukup → lolos. Tidak cukup → **lewati item itu** dengan alasan yang dicatat, lalu lanjut ke item berikutnya.
2. **`progress_hooks`** — jaring pengaman keras. Kalau satu item ternyata jauh lebih besar dari perkiraan dan melewati ambang, unduhan itu dibatalkan lewat `DownloadCancelled`.

Keuntungan pendekatan ini: task **tetap selesai `done`** dan membawa laporan `skipped` yang jelas, bukan gagal total setelah 44 dari 200 item.

### 7.3 Ambang penghentian
Berhenti melewati item ketika salah satu terpenuhi:

- sisa ruang disk < **512 MB**, atau
- sisa kuota harian klien < **64 MB**

Kedua angka ada di `app/core/config.py` sebagai `DISK_MIN_FREE_BYTES` dan `QUOTA_MIN_REMAINING_BYTES`.

### 7.4 Engine cadangan untuk foto
Urutan percobaan untuk **foto**: `YtDlpEngine` lebih dulu; kalau gagal (`UNSUPPORTED_PLATFORM` atau galat ekstraksi), lanjut ke `GalleryDlEngine`. Alasannya: gallery-dl lebih piawai menangani album foto. Wikimedia tetap langsung ke `GalleryDlEngine` seperti sekarang.

## 8. Foto

- Satuan terkecil tetap satu file; mekanisme banyak-file milik kumpulan dipakai ulang untuk carousel (satu task, banyak file).
- Tidak ada postprocessor audio kecuali `audio_only = true`.
- `kind` dikirim di respon `extract` dan per file di respon `tasks`.
- Ekstensi & MIME ditebak dari nama file hasil seperti sekarang (`mimetypes.guess_type`).

## 9. API key permanen

Sasaran: `key_vGV-CNOBdgm-7B6NL3NxGY-5vlQbmUutJYg1SPS0UQ8`.

Fondasi yang **sudah ada**: `app/main.py` membuat ulang klien "master" dengan hash SHA-256 kunci itu setiap server nyala, tanpa kolom kedaluwarsa. Karena kodenya selalu menyemai nilai yang sama, kuncinya **tetap sama walau VM dibangun ulang**.

Tiga penguat yang ditambahkan:

1. **Pindah ke `app/core/config.py`** sebagai `MASTER_API_KEY` (bisa di-set lewat variabel lingkungan, tidak lagi *hard-coded* di `main.py`).
2. **Tidak bisa mati diam-diam.** `ClientRepository.get_by_api_key_hash` menyaring `is_active = 1`. Bila suatu saat klien master jadi `is_active = 0`, kuncinya mati tanpa pesan. Setiap proses start akan memastikan: klien master ada, `is_active = 1`, `daily_bytes_quota = 100 TB`, `concurrency_limit = 50`.
3. **Uji otomatis** memastikan nilai hash kunci itu selalu ter-semi dan dapat ditemukan.

Yang **tetap** punya masa berlaku (sesuai keputusan no. 1):

| Hal | Masa berlaku | Setelan |
|---|---|---|
| `download_url` (token) | 15 menit | `TOKEN_TTL_SECONDS` |
| File hasil | auto-hapus 1 hari | `FILE_DEFAULT_TTL_SECONDS` |

## 10. Perubahan angka konfigurasi

| Setelan | Lama | Baru | Alasan |
|---|---|---|---|
| `STORAGE_MAX_BYTES` | 20 GB | **5 GB** | 🚨 lebih besar dari disk fisik 7,5 GB — sistem penjaga ruang mengira punya tempat 2,7× lebih banyak |
| `TASK_MAX_DURATION_SECONDS` | 900 | **900** (tetap) | URL tunggal |
| `TASK_MAX_DURATION_COLLECTION_SECONDS` | — | **3600** (baru) | kumpulan besar butuh waktu |
| `TASK_STALL_TIMEOUT_SECONDS` | 60 | **180** | jeda antar video lewat tunnel bisa panjang |
| `DISK_MIN_FREE_BYTES` | — | **512 MB** (baru) | ambang berhenti per item |
| `QUOTA_MIN_REMAINING_BYTES` | — | **64 MB** (baru) | ambang berhenti per item |
| `MASTER_API_KEY` | — | `key_vGV-…` (baru) | kunci permanen |

`TASK_MAX_DURATION_SECONDS` (15 menit) berlaku untuk URL tunggal; `TASK_MAX_DURATION_COLLECTION_SECONDS` (1 jam) dipakai hanya bila `collection` terdeteksi.

## 11. Penanganan galat

| Kondisi | Kode | HTTP | Pesan (ringkas) |
|---|---|---|---|
| Channel/profil dikirim | `unsupported_playlist` | 400 | "Channel/profil tidak didukung. Hanya playlist, album, dan carousel." |
| Mix otomatis `RD…` | `unsupported_playlist` | 400 | "Mix otomatis YouTube tidak didukung karena tidak berujung." |
| Kumpulan dilewati sebagian | — | 200 | task `done` + `skipped[]` berisi alasannya |
| Kuota harian habis | — | 200 | alasan `skipped[].reason` = `"daily_quota_exceeded"` |
| Disk hampir penuh | — | 200 | alasan `skipped[].reason` = `"insufficient_disk"` |
| Kedua mesin gagal untuk foto | `unsupported_platform` | 400 | seperti sekarang |

Kode galat **tidak ada yang ditambah atau dihapus** dari `ErrorCode`, supaya kontrak konsumen tidak berubah. Yang berubah hanya pesannya.

## 12. Pengujian

| File | Yang dites |
|---|---|
| `tests/test_validator.py` (file baru) | ±35 kasus URL nyata: tabel boleh/tolak per platform, sanitisasi tetap berjalan |
| `tests/test_engines.py` | kumpulan diterima; channel ditolak; `playlist_id == channel_id` ditolak; mix `RD` ditolak; `RDAM` lolos; foto → `kind=photo`; fallback ke gallery-dl |
| `tests/test_worker.py` | kuota per item: item kelewat **tercatat**, task tetap `done`; nama file per item |
| `tests/test_config_and_errors.py` | angka 5 GB / 3600 / 180 / 512 MB / 64 MB |
| `tests/test_tokens.py` | token kedaluwarsa tepat 15 menit; token untuk file lain ditolak |

Semua uji memakai `unittest` + `unittest.mock`, tanpa jaringan nyata, konsisten dengan yang sudah ada.

## 13. Verifikasi end-to-end di VM (bareng Pai)

1. Playlist YouTube 20 video → **20 file dalam 1 task**, masing-masing punya `download_url`
2. Channel YouTube `/@…` → **ditolak dalam milidetik**, 0 byte terunduh
3. Carousel Instagram → beberapa foto muncul sebagai file terpisah
4. Foto tunggal → 1 file dengan `kind: "photo"`
5. Playlist 200+ video → **berhenti sendiri** saat disk hampir penuh, laporan `skipped` rapi, task tetap `done`
6. Restart server → `key_vGV-…` **tetap berlaku**
7. Regresi: video YouTube tunggal, YT Music, TikTok, Twitter/X, Facebook, SoundCloud tetap jalan

## 14. Di luar cakupan (out of scope)

- Channel/profil (YouTube, Instagram, TikTok, Twitter/X)
- Thread/utas Twitter/X
- Kategori & galeri Wikimedia Commons
- ZIP bundel / unduh semua sekaligus
- Pemecahan satu kumpulan jadi banyak task
- File yang tidak dihapus otomatis (tetap 1 hari)
- `download_url` tanpa masa berlaku (tetap 15 menit)

## 15. File yang disentuh

| File | Perubahan |
|---|---|
| `app/core/config.py` | `MASTER_API_KEY`, `DISK_MIN_FREE_BYTES`, `QUOTA_MIN_REMAINING_BYTES`, ubah 3 angka |
| `app/core/validator.py` | ganti `DISALLOWED_PATH_PATTERNS` jadi tabel aturan per platform |
| `app/core/engines/base.py` | perluas antarmuka: `kind`, `collection`, kait kuota per item |
| `app/core/engines/ytdlp.py` | dukung kumpulan, deteksi `kind`, `extract_flat`, `match_filter` kuota, uji lapis 2 |
| `app/core/engines/gallerydl.py` | jadi target fallback foto |
| `app/core/worker.py` | pengecekan kuota per item, `skipped[]`, nama file per item, fallback mesin |
| `app/core/cleaner.py` | pengecekan ruang per item (bukan sekali di depan) |
| `app/core/storage.py` | bantuan baca sisa ruang disk |
| `app/main.py` | semai `MASTER_API_KEY` + jaminan `is_active` |
| `app/api/endpoints/tasks.py` | `collection`, `skipped[]`, `index`, `kind`, nama file per item |
| `tests/*` | lihat bagian 12 |
