import unittest
import time
from app.core.tokens import generate_download_token, verify_download_token


class TestTokens(unittest.TestCase):
    def test_token_generation_and_validation(self):
        file_id = "fl_1234567890abcdef1234567890abcdef"
        exp = int(time.time()) + 300
        token = generate_download_token(file_id, exp)

        self.assertTrue(verify_download_token(token, file_id))
        self.assertFalse(verify_download_token(token, "fl_different_id"))
        self.assertFalse(verify_download_token("invalid.token.payload", file_id))

    def test_expired_token(self):
        file_id = "fl_1234567890abcdef1234567890abcdef"
        exp = int(time.time()) - 10
        token = generate_download_token(file_id, exp)
        self.assertFalse(verify_download_token(token, file_id))

    def test_tampered_signature(self):
        file_id = "fl_1234567890abcdef1234567890abcdef"
        exp = int(time.time()) + 300
        token = generate_download_token(file_id, exp)
        tampered = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
        self.assertFalse(verify_download_token(tampered, file_id))


if __name__ == "__main__":
    unittest.main()

