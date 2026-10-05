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

        files = [
            os.path.join(out_dest, f)
            for f in os.listdir(out_dest)
            if os.path.isfile(os.path.join(out_dest, f))
        ]
        return files

