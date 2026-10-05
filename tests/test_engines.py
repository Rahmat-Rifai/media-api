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

    @patch("yt_dlp.YoutubeDL")
    def test_ytdlp_extract_success(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = {
            "title": "Sample Track",
            "uploader": "Sample Artist",
            "duration": 180,
            "thumbnail": "https://example.com/thumb.jpg",
            "filesize": 5000000,
            "formats": [
                {
                    "format_id": "http_mp3",
                    "ext": "mp3",
                    "format_note": "128k",
                    "vcodec": "none",
                    "acodec": "mp3",
                    "filesize": 5000000,
                }
            ],
        }
        mock_ydl_cls.return_value = mock_instance

        engine = YtDlpEngine()
        info = engine.extract_info("https://soundcloud.com/sample/track")
        self.assertEqual(info["title"], "Sample Track")
        self.assertEqual(len(info["formats"]), 1)
        self.assertFalse(info["formats"][0]["has_video"])
        self.assertTrue(info["formats"][0]["has_audio"])

    def test_gallerydl_single_item_enforcement(self):
        engine = GalleryDlEngine()
        with self.assertRaises(AppException) as ctx:
            engine.extract_info("https://commons.wikimedia.org/wiki/Category:Animals")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

    def test_gallerydl_single_item_extract_success(self):
        engine = GalleryDlEngine()
        info = engine.extract_info("https://commons.wikimedia.org/wiki/File:Monarch_In_May.jpg")
        self.assertEqual(info["title"], "File:Monarch_In_May.jpg")
        self.assertEqual(info["uploader"], "Wikimedia Commons")


if __name__ == "__main__":
    unittest.main()

