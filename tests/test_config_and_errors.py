import unittest
from app.core.config import Settings
from app.core.errors import ErrorCode, ErrorEnvelope, AppException

class TestConfigAndErrors(unittest.TestCase):
    def test_default_settings(self):
        settings = Settings(ADMIN_API_KEY="test-admin-key")
        self.assertEqual(settings.NETWORK_PROFILE, "standard")
        self.assertEqual(settings.STORAGE_MAX_BYTES, 20 * 1024 * 1024 * 1024)
        self.assertEqual(settings.HIGH_WATERMARK_RATIO, 0.90)
        self.assertEqual(settings.LOW_WATERMARK_RATIO, 0.70)
        self.assertEqual(settings.WORKER_CONCURRENCY, 3)

    def test_error_envelope_serialization(self):
        envelope = ErrorEnvelope.create(
            code=ErrorCode.UNSUPPORTED_PLATFORM,
            message="Domain not allowlisted",
            retryable=False
        )
        data = envelope.model_dump()
        self.assertEqual(data, {
            "error": {
                "code": "unsupported_platform",
                "message": "Domain not allowlisted",
                "retryable": False
            }
        })

    def test_app_exception(self):
        exc = AppException(
            status_code=400,
            code=ErrorCode.SSRF_DETECTED,
            message="IP literal rejected",
            retryable=False
        )
        self.assertEqual(exc.status_code, 400)
        self.assertEqual(exc.code, ErrorCode.SSRF_DETECTED)
        self.assertFalse(exc.retryable)

if __name__ == "__main__":
    unittest.main()
