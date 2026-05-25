from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from config.settings import Settings
from utils.logger import get_logger
from utils.security import normalize_phone
from utils.session_store import StoredSession


class PWThorAuthError(RuntimeError):
    """Raised when PWThor authentication fails."""


@dataclass(slots=True)
class PendingLogin:
    phone_number: str
    cookies: dict[str, str]


class PWThorClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)

    async def request_otp(self, phone_number: str) -> PendingLogin:
        phone = normalize_phone(phone_number)
        async with self._client() as client:
            response = await self._json_request(
                client,
                "POST",
                "/api/auth/login",
                json={"phoneNumber": phone},
            )
            if not response.get("success"):
                raise PWThorAuthError(str(response.get("message") or "OTP request failed."))
            return PendingLogin(phone_number=phone, cookies=dict(client.cookies))

    async def verify_otp(self, pending: PendingLogin, otp: str) -> StoredSession:
        if not otp.isdigit() or not 4 <= len(otp) <= 8:
            raise PWThorAuthError("OTP must be 4 to 8 digits.")

        async with self._client(cookies=pending.cookies) as client:
            response = await self._json_request(
                client,
                "POST",
                "/api/auth/verify-otp",
                json={"phoneNumber": pending.phone_number, "otp": otp},
            )
            if not response.get("success"):
                raise PWThorAuthError(str(response.get("message") or "OTP verification failed."))
            return StoredSession.new(
                phone_number=pending.phone_number,
                cookies=dict(client.cookies),
                user=response.get("user") if isinstance(response.get("user"), dict) else {},
            )

    async def _json_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                response = await client.request(method, path, **kwargs)
                data = response.json()
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "PWThor server error", request=response.request, response=response
                    )
                if not response.is_success:
                    message = data.get("message") if isinstance(data, dict) else None
                    raise PWThorAuthError(str(message or f"PWThor returned HTTP {response.status_code}."))
                if not isinstance(data, dict):
                    raise PWThorAuthError("PWThor returned an unexpected response.")
                return data
            except PWThorAuthError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt == self.settings.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 8))
        raise PWThorAuthError(f"PWThor authentication request failed: {last_error}") from last_error

    def _client(self, cookies: dict[str, str] | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.pwthor_base_url,
            cookies=cookies,
            follow_redirects=True,
            timeout=httpx.Timeout(self.settings.request_timeout_seconds),
            headers={
                "accept": "application/json, text/plain, */*",
                "content-type": "application/json",
                "origin": self.settings.pwthor_base_url,
                "referer": f"{self.settings.pwthor_base_url}/auth",
                "user-agent": "Mozilla/5.0",
            },
        )
