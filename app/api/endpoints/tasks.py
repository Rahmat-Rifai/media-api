from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from typing import Optional
import time
from app.api.deps import get_current_client, get_task_repo, get_file_repo, get_client_repo
from app.core.validator import validate_and_sanitize_url
from app.core.errors import ErrorCode, ErrorEnvelope
from app.core.tokens import generate_download_token
from app.db.repositories import TaskRepository, FileRepository, ClientRepository

router = APIRouter()


class TaskCreateRequest(BaseModel):
    url: str
    format_id: Optional[str] = None
    max_height: Optional[int] = None
    audio_only: Optional[bool] = False


@router.post("/tasks", status_code=status.HTTP_202_ACCEPTED)
def submit_task(
    body: TaskCreateRequest,
    request: Request,
    client: dict = Depends(get_current_client),
    task_repo: TaskRepository = Depends(get_task_repo),
    client_repo: ClientRepository = Depends(get_client_repo),
):
    active_count = client_repo.get_active_task_count(client["id"])
    if active_count >= client["concurrency_limit"]:
        raise HTTPException(
            status_code=429,
            detail=ErrorEnvelope.create(
                ErrorCode.CONCURRENCY_LIMIT_EXCEEDED, "Concurrency limit reached", retryable=True
            ).model_dump(),
        )

    clean_url = validate_and_sanitize_url(body.url)
    task = task_repo.create_task(
        client_id=client["id"],
        url=clean_url,
        format_id=body.format_id,
        max_height=body.max_height,
        audio_only=body.audio_only,
    )

    if hasattr(request.app.state, "worker") and request.app.state.worker:
        request.app.state.worker.enqueue_task(task["id"])

    return {
        "id": task["id"],
        "status": task["status"],
        "progress": task["progress"],
        "estimated_bytes": task["estimated_bytes"],
        "created_at": task["created_at"],
    }


@router.get("/tasks/{task_id}")
def get_task_status(
    task_id: str,
    client: dict = Depends(get_current_client),
    task_repo: TaskRepository = Depends(get_task_repo),
    file_repo: FileRepository = Depends(get_file_repo),
):
    task = task_repo.get_task(task_id)
    if not task or task["client_id"] != client["id"]:
        raise HTTPException(
            status_code=404,
            detail=ErrorEnvelope.create(ErrorCode.NOT_FOUND, "Task not found").model_dump(),
        )

    files_output = []
    if task["status"] == "done":
        files = file_repo.get_files_by_task(task_id)
        now_exp = int(time.time()) + 900
        for f in files:
            token = generate_download_token(f["id"], now_exp)
            files_output.append({
                "id": f["id"],
                "filename": f["original_title"],
                "mime_type": f["mime_type"],
                "size_bytes": f["size_bytes"],
                "download_url": f"/v1/files/{f['id']}?token={token}",
                "expires_at": f["expires_at"],
            })

    return {
        "id": task["id"],
        "status": task["status"],
        "progress": task["progress"],
        "estimated_bytes": task["estimated_bytes"],
        "actual_bytes": task["actual_bytes"],
        "error_code": task["error_code"],
        "error_message": task["error_message"],
        "files": files_output,
    }


@router.delete("/tasks/{task_id}")
def cancel_task(
    task_id: str,
    request: Request,
    client: dict = Depends(get_current_client),
    task_repo: TaskRepository = Depends(get_task_repo),
):
    task = task_repo.get_task(task_id)
    if not task or task["client_id"] != client["id"]:
        raise HTTPException(
            status_code=404,
            detail=ErrorEnvelope.create(ErrorCode.NOT_FOUND, "Task not found").model_dump(),
        )

    if hasattr(request.app.state, "worker") and request.app.state.worker:
        request.app.state.worker.cancel_task(task_id)
    else:
        task_repo.update_status(task_id, "cancelled")

    return {"id": task["id"], "status": "cancelled"}

