import os
from fastapi import APIRouter, HTTPException, Query, Depends
from starlette.responses import FileResponse
from app.api.deps import get_file_repo
from app.core.tokens import verify_download_token
from app.core.errors import ErrorCode, ErrorEnvelope
from app.db.repositories import FileRepository

router = APIRouter()


@router.get("/files/{file_id}")
def download_file(
    file_id: str,
    token: str = Query(...),
    file_repo: FileRepository = Depends(get_file_repo),
):
    if not verify_download_token(token, file_id):
        raise HTTPException(
            status_code=401,
            detail=ErrorEnvelope.create(ErrorCode.TOKEN_INVALID, "Invalid or expired token").model_dump(),
        )

    file_record = file_repo.get_file(file_id)
    if not file_record:
        raise HTTPException(
            status_code=410,
            detail=ErrorEnvelope.create(ErrorCode.FILE_EXPIRED, "File has expired or been evicted").model_dump(),
        )

    path = file_record["storage_path"]
    if not os.path.exists(path):
        raise HTTPException(
            status_code=410,
            detail=ErrorEnvelope.create(ErrorCode.FILE_EXPIRED, "File missing from disk").model_dump(),
        )

    file_repo.update_last_accessed(file_id)
    return FileResponse(
        path=path,
        filename=file_record["original_title"],
        media_type=file_record["mime_type"],
    )

