import ipaddress
import re
import urllib.parse
from app.core.config import settings
from app.core.errors import AppException, ErrorCode

IPV4_PATTERN = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")
HEX_OR_INT_IP_PATTERN = re.compile(r"^(0x[0-9a-fA-F]+|\d+)$")
DISALLOWED_PATH_PATTERNS = [
    re.compile(r"/sets(/|$)"),
    re.compile(r"/channels?(/|$)"),
    re.compile(r"/playlists?(/|$)"),
    re.compile(r"/albums?(/|$)"),
]
TRACKING_PARAMS = {
    "si",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "ref",
    "fbclid",
}


def is_ip_literal(host: str) -> bool:
    if host.startswith("[") and host.endswith("]"):
        return True
    if HEX_OR_INT_IP_PATTERN.match(host):
        return True
    clean_host = host.strip("[]").split("%")[0]
    try:
        ipaddress.ip_address(clean_host)
        return True
    except ValueError:
        pass
    if IPV4_PATTERN.match(host):
        return True
    return False


def is_domain_allowed(host: str) -> bool:
    host = host.lower()
    for allowed in settings.ALLOWLIST_DOMAINS:
        allowed = allowed.lower()
        if host == allowed or host.endswith("." + allowed):
            return True
    return False


def validate_and_sanitize_url(url: str) -> str:
    if not isinstance(url, str):
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "URL must be a non-empty string", retryable=False
        )

    url = url.strip()
    if not url:
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "URL must be a non-empty string", retryable=False
        )

    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except Exception:
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "Malformed URL format", retryable=False
        )

    if parsed.scheme.lower() != "https":
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "Only HTTPS URLs are allowed", retryable=False
        )

    if parsed.username or parsed.password or ("@" in parsed.netloc):
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "Userinfo in URL is forbidden", retryable=False
        )

    if port not in (None, 443):
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "Non-standard ports are forbidden", retryable=False
        )

    host = parsed.hostname or ""
    if not host or is_ip_literal(host):
        raise AppException(
            400, ErrorCode.SSRF_DETECTED, "IP literals and empty hosts are forbidden", retryable=False
        )

    if not is_domain_allowed(host):
        raise AppException(
            400,
            ErrorCode.UNSUPPORTED_PLATFORM,
            f"Domain '{host}' is not in the verified allowlist",
            retryable=False,
        )

    for pattern in DISALLOWED_PATH_PATTERNS:
        if pattern.search(parsed.path):
            raise AppException(
                400,
                ErrorCode.UNSUPPORTED_PLAYLIST,
                "Playlists, sets, and channels are not supported",
                retryable=False,
            )

    query_params = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    cleaned_params = [(k, v) for k, v in query_params if k.lower() not in TRACKING_PARAMS]
    new_query = urllib.parse.urlencode(cleaned_params)

    sanitized = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, new_query, "")
    )
    return sanitized
