successfully downloaded text file (SHA: 05d1b4f3f808f7f9d31d42a62cfb9c9681227522)
import os

import yt_dlp
from yt_dlp.utils import MaxDownloadsReached

from app.core.config import settings
from app.core.engines.base import MediaEngine
from app.core.errors import AppException, ErrorCode
from app.core.network import CurlRH

YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")

# Urutan "penyamaran klien" untuk YouTube.
#
# YouTube menaruh pemeriksaan bot ("Sign in to confirm you're not a bot") pada
# sebagian video ketika ia curiga yang datang adalah browser biasa dari IP yang
# tidak ia kenal. Tiap profil di bawah memakai player_client berbeda, sehingga
# yt-dlp mengirim User-Agent dan endpoint API yang berbeda pula -- persis seperti
# aplikasi Android / Smart TV yang asli.
#
# Profil dicoba satu per satu sampai ada yang tembus, lalu profil yang berhasil
# diingat agar permintaan berikutnya tidak mencoba dari awal lagi.
YOUTUBE_DISGUISES = [
    {"name": "android_vr", "player_client": ["android_vr"]},
    {"name": "tv", "player_client": ["tv"]},
    {"name": "tv_simply", "player_client": ["tv_simply"]},
    {"name": "web_embedded", "player_client": ["web_embedded"]},
    {"name": "web_safari", "player_client": ["web_safari"]},
    {"name": "web", "player_client": ["web"]},
    {"name": "mweb", "player_client": ["mweb"]},
    {"name": "default", "player_client": ["default"]},
]

BOT_CHECK_MARKERS = (
    "sign in to confirm",
    "not a bot",
    "confirm you're not",
    "login required",
    "use --cookies",
    "age-restricted",
    "age restricted",
)


def _is_bot_check(err_text: str) -> bool:
    low = (err_text or "").lower()
    return any(marker in low for marker in BOT_CHECK_MARKERS)


class YtDlpEngine(MediaEngine):
    # Nama disguise yang terakhir kali berhasil (None = belum ada / bukan YouTube).
    _preferred_disguise = None

    def _is_youtube(self, url: str) -> bool:
        low = (url or "").lower()
        return any(host in low for host in YOUTUBE_HOSTS)

    def _disguise_sequence(self, url: str) -> list:
        """Urutan profil penyamaran yang akan dicoba untuk URL ini."""
        if not self._is_youtube(url):
            return [None]
        order = list(YOUTUBE_DISGUISES)
        if self._preferred_disguise:
            # yang pernah berhasil didahulukan, tapi tetap ada cadangan
            order.sort(key=lambda d: d["name"] != self._preferred_disguise)
        return order

    def _get_ydl_opts(self, extra: dict = None, disguise: dict = None) -> dict:
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

        if disguise:
            opts["extractor_args"] = {
                "youtube": {"player_client": list(disguise["player_client"])}
            }

        # Jalan keluar terakhir kalau YouTube tetap bandel: cookie akun YouTube
        # yang sah bisa disimpan di file dan disebut lewat YT_COOKIES_FILE.
        cookiefile = (getattr(settings, "YT_COOKIES_FILE", "") or "").strip()
        if cookiefile and os.path.isfile(cookiefile):
            opts["cookiefile"] = cookiefile

        if extra:
            opts.update(extra)
        return opts

    def _create_ydl(self, opts: dict) -> yt_dlp.YoutubeDL:
        ydl = yt_dlp.YoutubeDL(opts)
        if settings.NETWORK_PROFILE == "sandbox":
            try:
                handler = CurlRH(logger=ydl)
                ydl._request_director.handlers = {"Curl": handler}
                ydl._request_director.default_handler = handler
            except Exception:
                # Jangan sampai gagal hanya karena pemasangan handler.
                pass
        return ydl

    def _fail(self, errors: list):
        texts = [str(e) for _, e in errors]
        joined = " | ".join(texts) if texts else "Unknown extraction error"
        if any("POST blocked" in t for t in texts):
            raise AppException(502, ErrorCode.EGRESS_POST_BLOCKED, "Upstream requires POST", retryable=False)
        if any(_is_bot_check(t) for t in texts):
            raise AppException(
                502,
                ErrorCode.UPSTREAM_BLOCKED,
                "YouTube menampilkan pemeriksaan bot (\"Sign in to confirm you're not a bot\") untuk "
                "video ini. Semua penyamaran klien sudah dicoba. Solusi paling manjur: simpan cookie "
                "akun YouTube ke lalu isi YT_COOKIES_FILE dengan path file cookie itu.",
                retryable=True,
            )
        if any("Account authentication" in t or "blocked" in t.lower() for t in texts):
            raise AppException(502, ErrorCode.UPSTREAM_BLOCKED, joined, retryable=False)
        raise AppException(400, ErrorCode.UNSUPPORTED_PLATFORM, joined, retryable=False)

    def _parse_info(self, info: dict) -> dict:
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
                "filesize_approx": f.get("filesize") or f.get("filesize_approx") or 0,
            })

        return {
            "title": info.get("title", "media"),
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "estimated_bytes": info.get("filesize") or info.get("filesize_approx") or 0,
            "formats": formats,
        }

    def extract_info(self, url: str) -> dict:
        errors = []
        for disguise in self._disguise_sequence(url):
            opts = self._get_ydl_opts({"extract_flat": False}, disguise)
            ydl = self._create_ydl(opts)
            try:
                info = ydl.extract_info(url, download=False)
            except AppException:
                raise
            except Exception as e:
                errors.append((disguise, e))
                continue

            self._preferred_disguise = disguise["name"] if disguise else None
            return self._parse_info(info)

        self._fail(errors)

    def _collect_files(self, out_dest: str) -> list[str]:
        return [
            os.path.join(out_dest, f)
            for f in os.listdir(out_dest)
            if os.path.isfile(os.path.join(out_dest, f))
        ]

    def _download_once(self, url: str, out_dest: str, format_id: str, audio_only: bool, disguise: dict, errors: list) -> list[str]:
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

        ydl = self._create_ydl(self._get_ydl_opts(extra_opts, disguise))
        try:
            ydl.extract_info(url, download=True)
        except MaxDownloadsReached:
            pass
        except AppException:
            raise
        except Exception as e:
            errors.append((disguise, e))
            return []

        return self._collect_files(out_dest)

    def download(self, url: str, out_dest: str, format_id: str = None, audio_only: bool = False) -> list[str]:
        errors = []
        for disguise in self._disguise_sequence(url):
            files = self._download_once(url, out_dest, format_id, audio_only, disguise, errors)
            if files:
                self._preferred_disguise = disguise["name"] if disguise else None
                return files

        self._fail(errors)
