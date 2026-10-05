import secrets
import hashlib
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from app.api.deps import require_admin, get_client_repo, get_file_repo
from app.db.repositories import ClientRepository, FileRepository

router = APIRouter()


class ClientCreateRequest(BaseModel):
    name: str
    daily_bytes_quota: int
    concurrency_limit: int = 1


@router.post("/admin/clients", status_code=status.HTTP_201_CREATED)
def create_client(
    body: ClientCreateRequest,
    admin_ok: bool = Depends(require_admin),
    client_repo: ClientRepository = Depends(get_client_repo),
):
    raw_api_key = f"key_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
    client = client_repo.create_client(
        name=body.name,
        api_key_hash=key_hash,
        daily_bytes_quota=body.daily_bytes_quota,
        concurrency_limit=body.concurrency_limit,
    )
    return {
        "id": client["id"],
        "name": client["name"],
        "api_key": raw_api_key,
        "daily_bytes_quota": client["daily_bytes_quota"],
        "concurrency_limit": client["concurrency_limit"],
    }


@router.get("/admin/storage")
def get_storage_stats(
    admin_ok: bool = Depends(require_admin),
    file_repo: FileRepository = Depends(get_file_repo),
):
    used = file_repo.get_total_storage_used()
    return {"used_bytes": used}


@router.post("/admin/cleaner/run")
def trigger_cleaner(
    admin_ok: bool = Depends(require_admin),
    file_repo: FileRepository = Depends(get_file_repo),
):
    from app.core.storage import StorageManager
    from app.core.cleaner import CleanerService

    storage_mgr = StorageManager()
    cleaner = CleanerService(file_repo=file_repo, storage_mgr=storage_mgr)
    return cleaner.run_eviction_cycle(force_emergency=False)

