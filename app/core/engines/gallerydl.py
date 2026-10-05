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
            "formats": [{
                "format_id": "original",
                "ext": "jpg",
                "quality": "original",
                "has_video": False,
                "has_audio": False,
                "filesize_approx": 1000000,
            }],
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

