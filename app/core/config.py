from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import List

class Settings(BaseSettings):
    NETWORK_PROFILE: str = Field(default="standard")
    DATA_PATH: str = Field(default="data")
    STORAGE_PATH: str = Field(default="storage")
    STORAGE_MAX_BYTES: int = Field(default=20 * 1024 * 1024 * 1024)
    HIGH_WATERMARK_RATIO: float = Field(default=0.90)
    LOW_WATERMARK_RATIO: float = Field(default=0.70)
    GRACE_PERIOD_SECONDS: int = Field(default=600)
    FILE_DEFAULT_TTL_SECONDS: int = Field(default=86400)
    WORKER_CONCURRENCY: int = Field(default=3)
    EXTRACT_CONCURRENCY: int = Field(default=2)
    TASK_MAX_DURATION_SECONDS: int = Field(default=900)
    TASK_STALL_TIMEOUT_SECONDS: int = Field(default=60)
    TOKEN_SECRET: str = Field(default="change-this-in-production-hmac-secret")
    TOKEN_TTL_SECONDS: int = Field(default=900)
    ADMIN_API_KEY: str = Field(default="admin-secret-key")
    ALLOWLIST_DOMAINS: List[str] = Field(default=[
        "soundcloud.com",
        "commons.wikimedia.org",
        "upload.wikimedia.org",
        "youtube.com",
        "youtu.be",
        "instagram.com",
        "tiktok.com",
        "twitter.com",
        "x.com",
        "facebook.com",
        "fb.watch",
    ])

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
