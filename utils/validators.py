from __future__ import annotations

import ipaddress
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


def validate_public_https_url(url: str, allowed_hosts: set[str]) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()

    if parsed.scheme != "https":
        raise URLValidationError("Only HTTPS links are allowed.")
    if parsed.username or parsed.password:
        raise URLValidationError("URLs with embedded credentials are not allowed.")
    if not host:
        raise URLValidationError("URL host is missing.")
    if _is_blocked_host(host):
        raise URLValidationError("Local or private network URLs are not allowed.")
    if "*" not in allowed_hosts and not _host_matches(host, allowed_hosts):
        raise URLValidationError("This source host is not allowed.")

    decoded_path = unquote(parsed.path)
    if any(part == ".." for part in decoded_path.split("/")):
        raise URLValidationError("Path traversal is not allowed.")
    return parsed.geturl()


def is_pwthor_url(url: str, base_url: str) -> bool:
    parsed = urlparse(url.strip())
    base_host = urlparse(base_url).hostname or "pwthor.live"
    host = (parsed.hostname or "").lower()
    return host == base_host or host.endswith(f".{base_host}")


def _host_matches(host: str, allowed_hosts: set[str]) -> bool:
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in allowed_hosts)


def _is_blocked_host(host: str) -> bool:
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
