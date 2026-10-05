from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.core.config import settings
from app.core.network import setup_network_profile
from app.core.errors import AppException, ErrorEnvelope
from app.db.database import init_db, get_db_connection
from app.db.repositories import TaskRepository, FileRepository, ClientRepository
from app.core.storage import StorageManager
from app.core.worker import WorkerService
from app.api.endpoints.extract import router as extract_router
from app.api.endpoints.tasks import router as tasks_router
from app.api.endpoints.files import router as files_router
from app.api.endpoints.admin import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_network_profile(settings.NETWORK_PROFILE)
    init_db()

    conn = get_db_connection()
    task_repo = TaskRepository(conn)
    task_repo.reset_running_tasks_on_startup()
    conn.close()

    storage_mgr = StorageManager()
    worker_conn = get_db_connection()
    worker = WorkerService(
        task_repo=TaskRepository(worker_conn),
        file_repo=FileRepository(worker_conn),
        client_repo=ClientRepository(worker_conn),
        storage_mgr=storage_mgr,
    )
    app.state.worker = worker

    yield

    worker_conn.close()


app = FastAPI(title="Media API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    envelope = ErrorEnvelope.create(code=exc.code, message=exc.message, retryable=exc.retryable)
    return JSONResponse(status_code=exc.status_code, content=envelope.model_dump())


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(extract_router, prefix="/v1")
app.include_router(tasks_router, prefix="/v1")
app.include_router(files_router, prefix="/v1")
app.include_router(admin_router, prefix="/v1")

