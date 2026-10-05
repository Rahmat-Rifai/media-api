import asyncio
import os
import mimetypes
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from app.core.config import settings
from app.core.storage import StorageManager
from app.core.cleaner import CleanerService
from app.core.errors import AppException, ErrorCode
from app.core.engines.ytdlp import YtDlpEngine
from app.core.engines.gallerydl import GalleryDlEngine
from app.db.repositories import TaskRepository, FileRepository, ClientRepository


class WorkerService:
    def __init__(
        self,
        task_repo: TaskRepository,
        file_repo: FileRepository,
        client_repo: ClientRepository,
        storage_mgr: StorageManager,
        cleaner_svc: Optional[CleanerService] = None,
    ):
        self.task_repo = task_repo
        self.file_repo = file_repo
        self.client_repo = client_repo
        self.storage_mgr = storage_mgr
        self.cleaner_svc = cleaner_svc or CleanerService(file_repo=file_repo, storage_mgr=storage_mgr)
        self.semaphore = asyncio.Semaphore(settings.WORKER_CONCURRENCY)
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.ytdlp_engine = YtDlpEngine()
        self.gallerydl_engine = GalleryDlEngine()

    def _get_engine_for_url(self, url: str):
        if "wikimedia.org" in url:
            return self.gallerydl_engine
        return self.ytdlp_engine

    def _resolve_short_url(self, url: str) -> str:
        """Resolve vt.tiktok.com / vm.tiktok.com short URLs via curl (tunneled).
        Returns original URL if not a short domain or resolution fails."""
        import subprocess
        import urllib.parse
        try:
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
        except Exception:
            return url
        if host not in ("vt.tiktok.com", "vm.tiktok.com"):
            return url
        try:
            # Use curl with proxy env (works through tunnel)
            import os
            env = {k: v for k, v in os.environ.items()}
            proc = subprocess.run(
                ["/usr/bin/curl", "-s", "-o", "/dev/null", "-w", "%{url_effective}",
                 "-L", "--max-time", "20", "--connect-timeout", "10", url],
                env=env, capture_output=True, timeout=25,
            )
            final = proc.stdout.decode().strip()
            if final and final != url:
                # Validate resolved domain is allowed
                from app.core.validator import is_domain_allowed
                fh = (urllib.parse.urlsplit(final).hostname or "").lower()
                if is_domain_allowed(fh):
                    return final
        except Exception:
            pass
        return url

    def _get_fallback_engine(self, url: str, error: Exception):
        """Return gallery-dl as fallback for Instagram photo posts.
        yt-dlp fails with 'No video formats found' for photos; gallery-dl handles them."""
        err_str = str(error).lower()
        if "instagram.com" in url and ("no video formats" in err_str or "empty media response" in err_str):
            return self.gallerydl_engine
        return None

    def cancel_task(self, task_id: str):
        if task_id in self.active_tasks:
            t = self.active_tasks[task_id]
            t.cancel()
        self.task_repo.update_status(task_id, "cancelled")
        self.storage_mgr.cleanup_tmp_dir(task_id)

    async def execute_task(self, task_id: str):
        async with self.semaphore:
            task = self.task_repo.get_task(task_id)
            if not task or task["status"] == "cancelled":
                return

            self.task_repo.update_status(task_id, "preparing")
            # Resolve short URLs (vt.tiktok.com etc.) to full URLs first
            task_url = self._resolve_short_url(task["url"])
            engine = self._get_engine_for_url(task_url)

            try:
                try:
                    info = engine.extract_info(task_url)
                except Exception as e:
                    # Fallback: Instagram photo posts fail in yt-dlp; try gallery-dl
                    fallback = self._get_fallback_engine(task_url, e)
                    if fallback is not None:
                        engine = fallback
                        info = engine.extract_info(task_url)
                    else:
                        raise
                est_bytes = info.get("estimated_bytes") or 5 * 1024 * 1024
                reserved = int(est_bytes * 2.2)

                # Disk space reservation rule
                self.cleaner_svc.reserve_space(reserved)
                self.task_repo.set_reservation(task_id, est_bytes, reserved)

                tmp_dir = self.storage_mgr.get_tmp_dir(task_id)
                files = engine.download(
                    task_url,
                    tmp_dir,
                    format_id=task.get("format_id"),
                    audio_only=bool(task.get("audio_only")),
                )

                if not files:
                    raise AppException(500, ErrorCode.INTERNAL_ERROR, "No files generated by download")

                total_actual = 0
                expires_at = (
                    datetime.now(timezone.utc) + timedelta(seconds=settings.FILE_DEFAULT_TTL_SECONDS)
                ).strftime("%Y-%m-%d %H:%M:%S")

                for fpath in files:
                    fname = os.path.basename(fpath)
                    fsize = os.path.getsize(fpath)
                    total_actual += fsize
                    ext = os.path.splitext(fname)[1]
                    file_id_name = f"fl_{os.urandom(16).hex()}{ext}"

                    dst_path = self.storage_mgr.commit_file_atomically(fpath, file_id_name)
                    mime, _ = mimetypes.guess_type(dst_path)

                    self.file_repo.create_file(
                        task_id=task_id,
                        client_id=task["client_id"],
                        filename=file_id_name,
                        original_title=info.get("title", fname),
                        mime_type=mime or "application/octet-stream",
                        size_bytes=fsize,
                        storage_path=dst_path,
                        expires_at=expires_at,
                    )

                self.client_repo.increment_daily_usage(task["client_id"], total_actual)
                self.task_repo.complete_task(task_id, total_actual)
                self.storage_mgr.cleanup_tmp_dir(task_id)

            except asyncio.CancelledError:
                self.task_repo.update_status(task_id, "cancelled")
                self.storage_mgr.cleanup_tmp_dir(task_id)
            except AppException as ae:
                self.task_repo.update_status(task_id, "failed", ae.code, ae.message)
                self.storage_mgr.cleanup_tmp_dir(task_id)
            except Exception as e:
                self.task_repo.update_status(task_id, "failed", ErrorCode.INTERNAL_ERROR.value, str(e))
                self.storage_mgr.cleanup_tmp_dir(task_id)
            finally:
                self.active_tasks.pop(task_id, None)

    def enqueue_task(self, task_id: str):
        t = asyncio.create_task(self.execute_task(task_id))
        self.active_tasks[task_id] = t

