import base64
import io
import os
import subprocess
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
from requests.models import Response as RequestsResponse
from urllib3.response import HTTPResponse

import yt_dlp
import yt_dlp.networking.common
from yt_dlp.networking.common import RequestHandler, Response as YtDlpResponse, register_rh, Features
from yt_dlp.networking.exceptions import HTTPError, TransportError
from app.core.config import settings


def _get_curl_env() -> dict:
    curl_env = dict(os.environ)
    if settings.NETWORK_PROFILE == "sandbox":
        curl_env.setdefault("http_proxy", "http://hatch-egress-proxy:3128")
        curl_env.setdefault("https_proxy", "http://hatch-egress-proxy:3128")
        curl_env.setdefault("HTTP_PROXY", "http://hatch-egress-proxy:3128")
        curl_env.setdefault("HTTPS_PROXY", "http://hatch-egress-proxy:3128")
    return curl_env


def _build_proxy_target(target_url: str, method: str, body_bytes: bytes | None) -> tuple[str, list[str]]:
    """Build tunneled proxy URL and headers for upstream Cloudflare Worker proxy."""
    upstream = settings.UPSTREAM_PROXY_URL.strip()
    headers_to_add = [
        "-H", f"X-Target-URL: {target_url}",
        "-H", f"X-Target-Method: {method}",
    ]
    query_params = {
        "url": target_url,
        "_method": method,
    }
    if body_bytes:
        body_b64 = base64.b64encode(body_bytes).decode("ascii")
        headers_to_add.extend(["-H", f"X-Target-Body-B64: {body_b64}"])
        query_params["_body_b64"] = body_b64

    qs = urllib.parse.urlencode(query_params)
    sep = "&" if "?" in upstream else "?"
    proxy_url = f"{upstream}{sep}{qs}"
    return proxy_url, headers_to_add


# Map target domains to their cookie-file setting names.
# Used to select the right cookies per request instead of blasting all
# cookies to every domain.
_COOKIE_FILE_BY_DOMAIN = (
    (("youtube.com", "youtu.be", "youtube-nocookie.com"), "YT_COOKIES_FILE"),
    (("facebook.com", "fb.watch", "fb.com"), "FB_COOKIES_FILE"),
    (("instagram.com",), "IG_COOKIES_FILE"),
    (("twitter.com", "x.com", "t.co"), "X_COOKIES_FILE"),
    (("tiktok.com",), "TT_COOKIES_FILE"),
)


def _cookie_file_for_url(url: str) -> str:
    """Return the configured cookie file path for the target domain, or ''."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    for domains, setting_name in _COOKIE_FILE_BY_DOMAIN:
        if any(host == d or host.endswith("." + d) for d in domains):
            path = (getattr(settings, setting_name, "") or "").strip()
            if path and os.path.isfile(path):
                return path
    return ""


def _load_cookie_header(cookiefile: str) -> str:
    """Parse Netscape cookie file into a Cookie header value.
    Needed because curl won't send e.g. youtube.com cookies to the Worker domain,
    but the Worker forwards headers to the target — so inject them manually.
    cookiefile comes from _cookie_file_for_url(); empty means no cookies
    configured for this domain (do NOT fall back to another platform's file)."""
    if not cookiefile or not os.path.isfile(cookiefile):
        return ""
    pairs = []
    try:
        with open(cookiefile, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    pairs.append(f"{parts[5]}={parts[6]}")
    except OSError:
        return ""
    return "; ".join(pairs)


class CurlRH(RequestHandler):
    RH_KEY = "Curl"
    RH_NAME = "curl"
    _SUPPORTED_URL_SCHEMES = ("http", "https")
    _SUPPORTED_PROXY_SCHEMES = ("http", "https")
    _SUPPORTED_FEATURES = (Features.ALL_PROXY, Features.NO_PROXY)

    def __init__(self, *args, logger=None, **kwargs):
        super().__init__(*args, logger=logger, **kwargs)

    def _send(self, request):
        method = (request.method or "GET").upper()
        upstream = settings.UPSTREAM_PROXY_URL.strip()

        if not upstream and method not in ("GET", "HEAD"):
            raise TransportError(f"Egress POST blocked by policy: {method}")

        cmd = ["/usr/bin/curl", "-s", "-i", "-L", "--connect-timeout", "15", "--max-time", "60"]

        # yt-dlp's cookiejar never reaches this handler as a Cookie header
        # (it bypasses urllib). Inject cookies manually: curl won't send
        # target-domain cookies to the Worker domain, but the Worker forwards
        # headers to the target, so a Cookie header flows through.
        # Without this, *_COOKIES_FILE is silently ignored on the tunneled path.
        # Cookies are selected per target domain (request.url is pre-proxy).
        cookiefile = _cookie_file_for_url(request.url)
        cookie_header = _load_cookie_header(cookiefile)
        if cookie_header:
            cmd.extend(["-H", f"Cookie: {cookie_header}"])
        # Also keep --cookie for the non-tunneled (direct) path.
        if cookiefile:
            cmd.extend(["--cookie", cookiefile])  # NOTE: never use --cookie-jar here; it would overwrite the user's file

        body_bytes = None
        if getattr(request, "data", None):
            if isinstance(request.data, bytes):
                body_bytes = request.data
            elif hasattr(request.data, "read"):
                body_bytes = request.data.read()
            elif isinstance(request.data, str):
                body_bytes = request.data.encode("utf-8")

        headers = dict(request.headers) if request.headers else {}
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        if upstream:
            request_url, proxy_headers = _build_proxy_target(request.url, method, body_bytes)
            cmd.extend(proxy_headers)
            for k, v in headers.items():
                if k.lower() not in ("connection", "accept-encoding", "content-length", "host"):
                    cmd.extend(["-H", f"{k}: {v}"])
        else:
            if method == "HEAD":
                cmd.append("-I")
            request_url = request.url
            for k, v in headers.items():
                if k.lower() not in ("connection", "accept-encoding", "content-length"):
                    cmd.extend(["-H", f"{k}: {v}"])

        cmd.extend(["-H", "Connection: close"])
        cmd.append(request_url)

        try:
            proc = subprocess.run(cmd, env=_get_curl_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=65)
        except subprocess.TimeoutExpired as e:
            raise TransportError(f"Curl timed out: {e}") from e

        out = proc.stdout
        delim = b"\r\n\r\n"
        blocks = out.split(delim)
        if len(blocks) < 2:
            delim = b"\n\n"
            blocks = out.split(delim)

        header_block = None
        body_idx = 0
        for idx, block in enumerate(blocks[:-1]):
            clean_block = block.lstrip()
            if clean_block.startswith(b"HTTP/"):
                if b"Connection Established" in clean_block and idx + 1 < len(blocks) - 1:
                    continue
                header_block = block
                body_idx = idx + 1

        if not header_block:
            raise TransportError(f"Failed to parse curl response from {request.url}")

        body = delim.join(blocks[body_idx:])
        lines = header_block.split(b"\r\n")
        if len(lines) == 1:
            lines = header_block.split(b"\n")
        status = int(lines[0].decode("latin1").split()[1])

        res_headers = {}
        for line in lines[1:]:
            line_str = line.decode("latin1")
            if ":" in line_str:
                k, v = line_str.split(":", 1)
                res_headers[k.strip()] = v.strip()

        res = YtDlpResponse(fp=io.BytesIO(body), headers=res_headers, url=request.url, status=status)
        if not (200 <= status < 300):
            raise HTTPError(res)
        return res


class CurlAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        method = (request.method or "GET").upper()
        upstream = settings.UPSTREAM_PROXY_URL.strip()

        if not upstream and method not in ("GET", "HEAD"):
            raise requests.exceptions.RequestException(f"Egress POST blocked by policy: {method}")

        cmd = ["/usr/bin/curl", "-s", "-i", "-L", "--connect-timeout", "15", "--max-time", "60"]

        # same cookie fix as CurlRH: requests' cookie jar never reaches curl.
        # Select per target domain and inject as Cookie header for the tunneled path.
        cookiefile = _cookie_file_for_url(request.url)
        cookie_header = _load_cookie_header(cookiefile)
        if cookie_header:
            cmd.extend(["-H", f"Cookie: {cookie_header}"])
        if cookiefile:
            cmd.extend(["--cookie", cookiefile])  # NOTE: never use --cookie-jar here; it would overwrite the user's file

        body_bytes = None
        if getattr(request, "body", None):
            if isinstance(request.body, bytes):
                body_bytes = request.body
            elif hasattr(request.body, "read"):
                body_bytes = request.body.read()
            elif isinstance(request.body, str):
                body_bytes = request.body.encode("utf-8")

        headers = dict(request.headers) if request.headers else {}
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        if upstream:
            request_url, proxy_headers = _build_proxy_target(request.url, method, body_bytes)
            cmd.extend(proxy_headers)
            for k, v in headers.items():
                if k.lower() not in ("connection", "accept-encoding", "content-length", "host"):
                    cmd.extend(["-H", f"{k}: {v}"])
        else:
            if method == "HEAD":
                cmd.append("-I")
            request_url = request.url
            for k, v in headers.items():
                if k.lower() not in ("connection", "accept-encoding", "content-length"):
                    cmd.extend(["-H", f"{k}: {v}"])

        cmd.extend(["-H", "Connection: close"])
        cmd.append(request_url)

        try:
            proc = subprocess.run(cmd, env=_get_curl_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=65)
        except subprocess.TimeoutExpired as e:
            raise requests.exceptions.Timeout(f"Curl timed out: {e}") from e

        out = proc.stdout
        delim = b"\r\n\r\n"
        blocks = out.split(delim)
        if len(blocks) < 2:
            delim = b"\n\n"
            blocks = out.split(delim)

        header_block = None
        body_idx = 0
        for idx, block in enumerate(blocks[:-1]):
            clean_block = block.lstrip()
            if clean_block.startswith(b"HTTP/"):
                if b"Connection Established" in clean_block and idx + 1 < len(blocks) - 1:
                    continue
                header_block = block
                body_idx = idx + 1

        if not header_block:
            raise requests.exceptions.ConnectionError("Failed to parse curl response")

        body = delim.join(blocks[body_idx:])
        lines = header_block.split(b"\r\n")
        if len(lines) == 1:
            lines = header_block.split(b"\n")
        status = int(lines[0].decode("latin1").split()[1])

        res_headers = {}
        for line in lines[1:]:
            line_str = line.decode("latin1")
            if ":" in line_str:
                k, v = line_str.split(":", 1)
                res_headers[k.strip()] = v.strip()

        raw_res = HTTPResponse(body=io.BytesIO(body), headers=res_headers, status=status, preload_content=False)
        resp = RequestsResponse()
        resp.status_code = status
        resp.headers.update(res_headers)
        resp.raw = raw_res
        resp.url = request.url
        resp.request = request
        return resp


def setup_network_profile(profile: str | None = None):
    if profile is None:
        profile = settings.NETWORK_PROFILE
    if profile == "sandbox":
        try:
            yt_dlp.networking.common.register_rh(CurlRH)
        except AssertionError:
            pass
        import gallery_dl.extractor.common

        gallery_dl.extractor.common._build_requests_adapter = lambda *a, **k: CurlAdapter()
