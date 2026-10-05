import os
import logging
from typing import Dict, Any
from app.core.config import settings
from app.core.storage import StorageManager
from app.core.errors import AppException, ErrorCode
from app.db.repositories import FileRepository

logger = logging.getLogger(__name__)


class CleanerService:
    def __init__(
        self,
        file_repo: FileRepository,
        storage_mgr: StorageManager,
        max_bytes: int = None,
        low_watermark: int = None,
        high_watermark: int = None,
        grace_period: int = None,
    ):
        self.file_repo = file_repo
        self.storage_mgr = storage_mgr
        self.max_bytes = max_bytes or settings.STORAGE_MAX_BYTES
        self.high_watermark = high_watermark or int(self.max_bytes * settings.HIGH_WATERMARK_RATIO)
        self.low_watermark = low_watermark or int(self.max_bytes * settings.LOW_WATERMARK_RATIO)
        self.grace_period = grace_period if grace_period is not None else settings.GRACE_PERIOD_SECONDS

    def run_eviction_cycle(self, force_emergency: bool = False) -> Dict[str, Any]:
        total_used = self.file_repo.get_total_storage_used()
        logger.info(f"Cleaner check: used {total_used} / max {self.max_bytes}")

        evicted_count = 0
        freed_bytes = 0

        if total_used >= self.high_watermark or force_emergency:
            # If force_emergency is true, grace period is 0 (evicts even recently accessed files)
            effective_grace = 0 if force_emergency else self.grace_period
            candidates = self.file_repo.list_evictable_files(effective_grace)

            for f in candidates:
                if total_used <= self.low_watermark and not force_emergency:
                    break
                path = f["storage_path"]
                if os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError as e:
                        logger.error(f"Failed to unlink {path}: {e}")
                self.file_repo.delete_file(f["id"])
                total_used -= f["size_bytes"]
                freed_bytes += f["size_bytes"]
                evicted_count += 1

        return {
            "evicted_count": evicted_count,
            "freed_bytes": freed_bytes,
            "current_storage_used": total_used,
        }

    def reserve_space(self, reserved_bytes: int) -> bool:
        total_used = self.file_repo.get_total_storage_used()
        if total_used + reserved_bytes > self.high_watermark:
            stats = self.run_eviction_cycle(force_emergency=False)
            total_used = stats["current_storage_used"]

        if total_used + reserved_bytes > self.max_bytes:
            stats = self.run_eviction_cycle(force_emergency=True)
            total_used = stats["current_storage_used"]

        if total_used + reserved_bytes > self.max_bytes:
            raise AppException(
                507,
                ErrorCode.QUOTA_EXCEEDED,
                "Insufficient storage capacity for task reservation",
                retryable=False,
            )
        return True

