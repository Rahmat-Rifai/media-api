from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.api.deps import get_current_client
from app.core.validator import validate_and_sanitize_url
from app.core.engines.ytdlp import YtDlpEngine
from app.core.engines.gallerydl import GalleryDlEngine

router = APIRouter()
ytdlp = YtDlpEngine()
gallerydl = GalleryDlEngine()


class ExtractRequest(BaseModel):
    url: str


@router.post("/extract")
def extract_metadata(body: ExtractRequest, client: dict = Depends(get_current_client)):
    clean_url = validate_and_sanitize_url(body.url)
    if "wikimedia.org" in clean_url:
        return gallerydl.extract_info(clean_url)
    return ytdlp.extract_info(clean_url)

