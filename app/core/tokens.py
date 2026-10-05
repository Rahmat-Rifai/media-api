import hmac
import hashlib
import base64
import time
from app.core.config import settings


def _sign(payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def generate_download_token(file_id: str, expires_at_timestamp: int, secret: str = None) -> str:
    secret = secret or settings.TOKEN_SECRET
    payload = f"{file_id}.{expires_at_timestamp}"
    signature = _sign(payload, secret)
    token_str = f"{payload}.{signature}"
    return base64.urlsafe_b64encode(token_str.encode("utf-8")).decode("utf-8")


def verify_download_token(token: str, file_id: str, secret: str = None) -> bool:
    secret = secret or settings.TOKEN_SECRET
    try:
        raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = raw.split(".")
        if len(parts) != 3:
            return False
        token_file_id, exp_str, signature = parts
        if not hmac.compare_digest(token_file_id, file_id):
            return False
        exp = int(exp_str)
        if time.time() > exp:
            return False
        expected_sig = _sign(f"{token_file_id}.{exp_str}", secret)
        return hmac.compare_digest(signature, expected_sig)
    except Exception:
        return False

