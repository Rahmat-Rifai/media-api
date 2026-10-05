import unittest
from app.core.validator import validate_and_sanitize_url
from app.core.errors import AppException, ErrorCode


class TestPlatformMatrix(unittest.TestCase):
    def test_allowlisted_platforms_pass_validator(self):
        urls = [
            "https://soundcloud.com/octobersveryown/drake-back-to-back-freestyle",
            "https://commons.wikimedia.org/wiki/File:Monarch_In_May.jpg",
            "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            "https://youtu.be/aqz-KE-bpKQ",
            "https://www.instagram.com/p/DAq_xyz/",
            "https://www.tiktok.com/@user/video/1234567890",
            "https://x.com/NASA/status/1841893322122608930",
        ]
        for u in urls:
            self.assertTrue(validate_and_sanitize_url(u))

    def test_non_goal_platforms_fail_validator(self):
        blocked = [
            "https://www.dailymotion.com/video/x7tgad0",
            "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8",
            "https://www.twitch.tv/videos/12345",
        ]
        for b in blocked:
            with self.assertRaises(AppException) as ctx:
                validate_and_sanitize_url(b)
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLATFORM)


if __name__ == "__main__":
    unittest.main()
