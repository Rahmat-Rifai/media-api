import os
import shutil
from typing import Optional
from app.core.config import settings
from app.core.errors import AppException, ErrorCode


class StorageManager:
    def __init__(self, base_path: str = None):
        self.base_path = base_path or settings.STORAGE_PATH
        self.tmp_base = os.path.join(self.base_path, "tmp")
        self.files_base = os.path.join(self.base_path, "files")
        os.makedirs(self.tmp_base, exist_ok=True)
        os.makedirs(self.files_base, exist_ok=True)

    def get_tmp_dir(self, task_id: str) -> str:
        d = os.path.join(self.tmp_base, task_id)
        os.makedirs(d, exist_ok=True)
        return d

    def cleanup_tmp_dir(self, task_id: str):
        d = os.path.join(self.tmp_base, task_id)
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)

    def get_files_dir(self) -> str:
        return self.files_base

    def get_file_path(self, filename: str) -> str:
        return os.path.join(self.files_base, filename)

    def commit_file_atomically(self, src_path: str, dst_filename: str) -> str:
        dst = os.path.join(self.files_base, dst_filename)
        if not os.path.exists(src_path):
            raise AppException(500, ErrorCode.INTERNAL_ERROR, f"Source file does not exist: {src_path}")
        os.replace(src_path, dst)
        return dst

    def get_dir_size(self, path: str) -> int:
        total = 0
        if not os.path.exists(path):
            return 0
        for root, dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

