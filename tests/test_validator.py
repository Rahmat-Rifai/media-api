import unittest
from app.core.validator import validate_and_sanitize_url
from app.core.errors import AppException, ErrorCode

class TestValidator(unittest.TestCase):
    def test_valid_urls(self):
        url = "https://soundcloud.com/octobersveryown/drake-back-to-back-freestyle?si=123&utm_source=copy"
        sanitized = validate_and_sanitize_url(url)
        self.assertEqual(sanitized, "https://soundcloud.com/octobersveryown/drake-back-to-back-freestyle")

        wiki_url = "https://commons.wikimedia.org/wiki/File:Monarch_In_May.jpg"
        self.assertEqual(validate_and_sanitize_url(wiki_url), wiki_url)

    def test_reject_scheme(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("http://soundcloud.com/track")
        self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_reject_ip_literals(self):
        ips = [
            "https://127.0.0.1/test",
            "https://169.254.169.254/latest/meta-data",
            "https://10.0.0.1/test",
            "https://[::1]/test",
            "https://0x7f000001/test"
        ]
        for ip in ips:
            with self.assertRaises(AppException) as ctx:
                validate_and_sanitize_url(ip)
            self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_reject_userinfo_and_ports(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://user:pass@soundcloud.com/track")
        self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://soundcloud.com:8443/track")
        self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_reject_unallowlisted_domain(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://evil.com/video")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLATFORM)

        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://not-soundcloud.com/track")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLATFORM)

    def test_reject_pure_playlist(self):
        playlist_urls = [
            "https://soundcloud.com/artist/sets/my-playlist",
            "https://soundcloud.com/artist/albums/my-album",
            "https://soundcloud.com/playlist/something",
            "https://soundcloud.com/channel/something",
        ]
        for p_url in playlist_urls:
            with self.assertRaises(AppException) as ctx:
                validate_and_sanitize_url(p_url)
            self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

    def test_empty_and_invalid_inputs(self):
        invalid_inputs = ["", None, 12345, "   "]
        for item in invalid_inputs:
            with self.assertRaises(AppException) as ctx:
                validate_and_sanitize_url(item)
            self.assertEqual(ctx.exception.code, ErrorCode.SSRF_DETECTED)

    def test_tracking_params_cleaned_preserves_legitimate_params(self):
        url = "https://soundcloud.com/artist/track?si=abc&legit_param=keepme&utm_campaign=xyz"
        sanitized = validate_and_sanitize_url(url)
        self.assertEqual(sanitized, "https://soundcloud.com/artist/track?legit_param=keepme")


    def test_encoded_playlist_and_leading_dot(self):
        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://soundcloud.com/artist/%73%65%74%73/my-playlist")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLAYLIST)

        with self.assertRaises(AppException) as ctx:
            validate_and_sanitize_url("https://.soundcloud.com/track")
        self.assertEqual(ctx.exception.code, ErrorCode.UNSUPPORTED_PLATFORM)

    def test_utm_wildcard_stripping(self):
        url = "https://soundcloud.com/artist/track?utm_custom_id=xyz&keep_me=1"
        sanitized = validate_and_sanitize_url(url)
        self.assertEqual(sanitized, "https://soundcloud.com/artist/track?keep_me=1")

if __name__ == "__main__":
    unittest.main()
