import unittest
from unittest.mock import patch, MagicMock
from app.core.engines.ytdlp import YtDlpEngine, YOUTUBE_DISGUISES
from app.core.engines.gallerydl import GalleryDlEngine
from app.core.errors import AppException, ErrorCode

GOOD_INFO = {
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
        mock_instance.extract_info.return_value = dict(GOOD_INFO)
        mock_ydl_cls.return_value = mock_instance

        engine = YtDlpEngine()
        info = engine.extract_info("https://soundcloud.com/sample/track")
        self.assertEqual(info["title"], "Sample Track")
        self.assertEqual(len(info["formats"]), 1)
        self.assertFalse(info["formats"][0]["has_video"])
        self.assertTrue(info["formats"][0]["has_audio"])

    @patch("yt_dlp.YoutubeDL")
    def test_non_youtube_gets_no_disguise(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = dict(GOOD_INFO)
        mock_ydl_cls.return_value = mock_instance

        engine = YtDlpEngine()
        engine.extract_info("https://soundcloud.com/sample/track")
        opts = mock_ydl_cls.call_args[0][0]
        self.assertNotIn("extractor_args", opts)

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_starts_with_android_vr_disguise(self, mock_ydl_cls):
        mock_instance = MagicMock()
        mock_instance.extract_info.return_value = dict(GOOD_INFO)
        mock_ydl_cls.return_value = mock_instance

        engine = YtDlpEngine()
        engine.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        opts = mock_ydl_cls.call_args[0][0]
        # First disguise is now api_android (skip_webpage variant)
        self.assertEqual(opts["extractor_args"]["youtube"]["player_client"], ["android"])

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_retries_next_disguise_on_bot_check(self, mock_ydl_cls):
        failing = MagicMock()
        failing.extract_info.side_effect = Exception("ERROR: Sign in to confirm you're not a bot")
        ok = MagicMock()
        ok.extract_info.return_value = dict(GOOD_INFO)
        mock_ydl_cls.side_effect = [failing, ok]

        engine = YtDlpEngine()
        info = engine.extract_info("https://www.youtube.com/watch?v=aqz-KE-bpKQ")

        self.assertEqual(info["title"], "Sample Track")
        self.assertEqual(mock_ydl_cls.call_count, 2)
        second_opts = mock_ydl_cls.call_args_list[1][0][0]
        self.assertEqual(second_opts["extractor_args"]["youtube"]["player_client"], ["tv"])

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_remembers_working_disguise(self, mock_ydl_cls):
        failing = MagicMock()
        failing.extract_info.side_effect = Exception("Sign in to confirm you're not a bot")
        ok = MagicMock()
        ok.extract_info.return_value = dict(GOOD_INFO)
        mock_ydl_cls.side_effect = [failing, ok, ok]

        engine = YtDlpEngine()
        engine.extract_info("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        engine.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ")

        third_opts = mock_ydl_cls.call_args_list[2][0][0]
        self.assertEqual(third_opts["extractor_args"]["youtube"]["player_client"], ["tv"])

    @patch("yt_dlp.YoutubeDL")
    def test_youtube_all_disguises_fail_raises_upstream_blocked(self, mock_ydl_cls):
        inst = MagicMock()
        inst.extract_info.side_effect = Exception("Sign in to confirm you're not a bot")
        mock_ydl_cls.return_value = inst

        engine = YtDlpEngine()
        with self.assertRaises(AppException) as ctx:
            engine.extract_info("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
        self.assertEqual(ctx.exception.code, ErrorCode.UPSTREAM_BLOCKED)
        self.assertEqual(mock_ydl_cls.call_count, len(YOUTUBE_DISGUISES))

    @patch("yt_dlp.YoutubeDL")
    def test_download_falls_back_to_next_disguise(self, mock_ydl_cls):
        import tempfile, os
        failing = MagicMock()
        failing.extract_info.side_effect = Exception("Sign in to confirm you're not a bot")
        ok = MagicMock()

        def _write_file(url, download=False):
            with open(os.path.join(dest.name, "abc123.mp4"), "w") as fh:
                fh.write("x")
            return dict(GOOD_INFO)

        ok.extract_info.side_effect = _write_file
        mock_ydl_cls.side_effect = [failing, ok]

        engine = YtDlpEngine()
        with tempfile.TemporaryDirectory() as dest_name:
            dest = MagicMock()
            dest.name = dest_name
            files = engine.download("https://www.youtube.com/watch?v=dQw4w9WgXcQ", dest_name)
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].endswith("abc123.mp4"))

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
