import io
import os
import subprocess
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
        if method not in ("GET", "HEAD"):
            raise TransportError(f"Egress POST blocked by policy: {method}")

        cmd = ["/usr/bin/curl", "-s", "-i", "-L", "--connect-timeout", "10", "--max-time", "30"]
        if method == "HEAD":
            cmd.append("-I")

        headers = dict(request.headers) if request.headers else {}
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        for k, v in headers.items():
            if k.lower() not in ("connection", "accept-encoding", "content-length"):
                cmd.extend(["-H", f"{k}: {v}"])
        cmd.extend(["-H", "Connection: close"])
        cmd.append(request.url)

        try:
            proc = subprocess.run(cmd, env=_get_curl_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
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
            if block.startswith(b"HTTP/"):
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
        if method not in ("GET", "HEAD"):
            raise requests.exceptions.RequestException(f"Egress POST blocked by policy: {method}")

        cmd = ["/usr/bin/curl", "-s", "-i", "-L", "--connect-timeout", "10", "--max-time", "30"]
        if method == "HEAD":
            cmd.append("-I")

        headers = dict(request.headers) if request.headers else {}
        if "User-Agent" not in headers and "user-agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

        for k, v in headers.items():
            if k.lower() not in ("connection", "accept-encoding", "content-length"):
                cmd.extend(["-H", f"{k}: {v}"])
        cmd.extend(["-H", "Connection: close"])
        cmd.append(request.url)

        try:
            proc = subprocess.run(cmd, env=_get_curl_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=35)
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
            if block.startswith(b"HTTP/"):
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

