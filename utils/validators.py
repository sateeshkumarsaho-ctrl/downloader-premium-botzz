from __future__ import annotations

from urllib.parse import unquote, urlparse


class URLValidationError(ValueError):
    """Raised for rejected user URLs."""


def validate_pwthor_url(url: str, base_url: str) -> str:
    parsed = urlparse(url.strip())
    base_host = urlparse(base_url).hostname or "pwthor.live"
    host = parsed.hostname or ""

    if parsed.scheme != "https":
        raise URLValidationError("Only HTTPS links are allowed.")
    if parsed.username or parsed.password:
        raise URLValidationError("URLs with embedded credentials are not allowed.")
    if host != base_host and not host.endswith(f".{base_host}"):
        raise URLValidationError("Only pwthor.live links are allowed.")
    decoded_path = unquote(parsed.path)
    if any(part == ".." for part in decoded_path.split("/")):
        raise URLValidationError("Path traversal is not allowed.")
    return parsed.geturl()
