import hashlib
import hmac
from typing import Optional
from fastapi import Header, HTTPException, Depends
from app.core.config import settings
from app.core.errors import ErrorCode, ErrorEnvelope
from app.db.database import get_db_connection
from app.db.repositories import ClientRepository, TaskRepository, FileRepository


def get_db():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_client_repo(conn=Depends(get_db)) -> ClientRepository:
    return ClientRepository(conn)


def get_task_repo(conn=Depends(get_db)) -> TaskRepository:
    return TaskRepository(conn)


def get_file_repo(conn=Depends(get_db)) -> FileRepository:
    return FileRepository(conn)


def verify_client_key(x_api_key: Optional[str], client_repo: ClientRepository) -> dict:
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail=ErrorEnvelope.create(ErrorCode.UNAUTHORIZED, "X-API-Key header required").model_dump(),
        )
    key_hash = hashlib.sha256(x_api_key.encode()).hexdigest()
    client = client_repo.get_by_api_key_hash(key_hash)
    if not client:
        raise HTTPException(
            status_code=401,
            detail=ErrorEnvelope.create(ErrorCode.UNAUTHORIZED, "Invalid API key").model_dump(),
        )
    return client


def get_current_client(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    client_repo: ClientRepository = Depends(get_client_repo),
) -> dict:
    return verify_client_key(x_api_key, client_repo)


def verify_admin_key(x_admin_key: Optional[str], expected_key: Optional[str] = None) -> bool:
    expected = expected_key or settings.ADMIN_API_KEY
    if not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise HTTPException(
            status_code=403,
            detail=ErrorEnvelope.create(ErrorCode.UNAUTHORIZED, "Invalid admin key").model_dump(),
        )
    return True


def require_admin(x_admin_key: Optional[str] = Header(None, alias="X-Admin-Key")) -> bool:
    return verify_admin_key(x_admin_key)

