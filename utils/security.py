from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path

from cryptography.fernet import Fernet


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if not re.fullmatch(r"[6-9]\d{9}", digits):
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    return digits


def build_fernet_key(bot_token: str, api_hash: str, session_secret: str | None) -> bytes:
    material = session_secret or f"{bot_token}:{api_hash}"
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_json(data: bytes, bot_token: str, api_hash: str, session_secret: str | None) -> bytes:
    return Fernet(build_fernet_key(bot_token, api_hash, session_secret)).encrypt(data)


def decrypt_json(data: bytes, bot_token: str, api_hash: str, session_secret: str | None) -> bytes:
    return Fernet(build_fernet_key(bot_token, api_hash, session_secret)).decrypt(data)


def safe_join(base: Path, *parts: str) -> Path:
    resolved_base = base.resolve()
    candidate = resolved_base.joinpath(*parts).resolve()
    if os.path.commonpath([resolved_base, candidate]) != str(resolved_base):
        raise ValueError("Unsafe path rejected.")
    return candidate


def sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(" ._")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "pwthor-video"
