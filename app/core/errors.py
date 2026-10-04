from enum import Enum
from pydantic import BaseModel

class ErrorCode(str, Enum):
    SSRF_DETECTED = "ssrf_detected"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    UNSUPPORTED_PLAYLIST = "unsupported_playlist"
    UNSUPPORTED_STREAM = "unsupported_stream"
    UPSTREAM_BLOCKED = "upstream_blocked"
    EGRESS_POST_BLOCKED = "egress_post_blocked"
    CONCURRENCY_LIMIT_EXCEEDED = "concurrency_limit_exceeded"
    QUEUE_FULL = "queue_full"
    DAILY_QUOTA_EXCEEDED = "daily_quota_exceeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    TASK_TIMEOUT = "task_timeout"
    TASK_STALLED = "task_stalled"
    TASK_CANCELLED = "task_cancelled"
    FILE_EXPIRED = "file_expired"
    TOKEN_INVALID = "token_invalid"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    INTERNAL_ERROR = "internal_error"

class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool

class ErrorEnvelope(BaseModel):
    error: ErrorDetail

    @classmethod
    def create(cls, code: ErrorCode | str, message: str, retryable: bool = False) -> "ErrorEnvelope":
        code_str = code.value if isinstance(code, ErrorCode) else str(code)
        return cls(error=ErrorDetail(code=code_str, message=message, retryable=retryable))

class AppException(Exception):
    def __init__(self, status_code: int, code: ErrorCode | str, message: str, retryable: bool = False):
        self.status_code = status_code
        self.code = code.value if isinstance(code, ErrorCode) else str(code)
        self.message = message
        self.retryable = retryable
        super().__init__(self.message)
