from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.settings import Settings
from utils.security import decrypt_json, encrypt_json, safe_join


@dataclass(slots=True)
class StoredSession:
    phone_number: str
    cookies: dict[str, str]
    user: dict[str, Any]
    created_at: float
    last_used_at: float

    @classmethod
    def new(
        cls,
        phone_number: str,
        cookies: dict[str, str],
        user: dict[str, Any] | None = None,
    ) -> "StoredSession":
        now = time.time()
        return cls(
            phone_number=phone_number,
            cookies=cookies,
            user=user or {},
            created_at=now,
            last_used_at=now,
        )

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "phone_number": self.phone_number,
                "cookies": self.cookies,
                "user": self.user,
                "created_at": self.created_at,
                "last_used_at": self.last_used_at,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> "StoredSession":
        data = json.loads(raw.decode("utf-8"))
        return cls(
            phone_number=str(data["phone_number"]),
            cookies={str(k): str(v) for k, v in data["cookies"].items()},
            user=data.get("user") if isinstance(data.get("user"), dict) else {},
            created_at=float(data["created_at"]),
            last_used_at=float(data["last_used_at"]),
        )


class SessionStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.sessions_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, telegram_user_id: int, session: StoredSession) -> None:
        path = self._path_for(telegram_user_id)
        tmp_path = path.with_suffix(".tmp")
        encrypted = encrypt_json(
            session.to_json(),
            self.settings.bot_token,
            self.settings.api_hash,
            self.settings.session_secret,
        )

        def _write() -> None:
            tmp_path.write_bytes(encrypted)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, path)

        await asyncio.to_thread(_write)

    async def load(self, telegram_user_id: int) -> StoredSession | None:
        path = self._path_for(telegram_user_id)
        if not path.exists():
            return None

        def _read() -> StoredSession | None:
            decrypted = decrypt_json(
                path.read_bytes(),
                self.settings.bot_token,
                self.settings.api_hash,
                self.settings.session_secret,
            )
            session = StoredSession.from_json(decrypted)
            if time.time() - session.last_used_at > self.settings.session_ttl_days * 86400:
                path.unlink(missing_ok=True)
                return None
            return session

        return await asyncio.to_thread(_read)

    async def touch(self, telegram_user_id: int) -> None:
        session = await self.load(telegram_user_id)
        if session:
            session.last_used_at = time.time()
            await self.save(telegram_user_id, session)

    async def delete(self, telegram_user_id: int) -> None:
        path = self._path_for(telegram_user_id)
        await asyncio.to_thread(path.unlink, missing_ok=True)

    async def cleanup_expired(self) -> None:
        cutoff = time.time() - self.settings.session_ttl_days * 86400

        def _cleanup() -> None:
            for path in self.settings.sessions_dir.glob("*.enc"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)

        await asyncio.to_thread(_cleanup)

    def _path_for(self, telegram_user_id: int) -> Path:
        return safe_join(self.settings.sessions_dir, f"{int(telegram_user_id)}.json.enc")
