from __future__ import annotations

import asyncio
import html
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import unquote, urljoin, urlparse

import httpx

from config.settings import Settings
from utils.logger import get_logger
from utils.security import safe_join, sanitize_filename
from utils.session_store import StoredSession
from utils.validators import validate_pwthor_url

ProgressCallback = Callable[[str], Awaitable[None]]


class DownloadError(RuntimeError):
    """Raised for recoverable download failures."""


@dataclass(slots=True)
class DownloadResult:
    path: Path
    title: str
    size_bytes: int


class PWThorDownloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = get_logger(__name__)

    async def download(
        self,
        telegram_user_id: int,
        url: str,
        session: StoredSession,
        cancel_event: asyncio.Event,
        progress: ProgressCallback,
    ) -> DownloadResult:
        source_url = validate_pwthor_url(url, self.settings.pwthor_base_url)
        media_url, title = await self.resolve_media_url(source_url, session)

        user_dir = safe_join(self.settings.downloads_dir, str(telegram_user_id))
        user_dir.mkdir(parents=True, exist_ok=True)
        output = safe_join(user_dir, f"{sanitize_filename(title)}-{uuid.uuid4().hex[:10]}.mp4")

        await progress("Download started.")
        last_error: DownloadError | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            output.unlink(missing_ok=True)
            try:
                await self._run_ffmpeg(media_url, output, session, cancel_event, progress)
                last_error = None
                break
            except DownloadError as exc:
                last_error = exc
                if cancel_event.is_set() or attempt >= self.settings.max_retries:
                    break
                await progress(f"Download attempt {attempt} failed. Retrying...")
                await asyncio.sleep(min(2**attempt, 8))
        if last_error:
            raise last_error

        size = output.stat().st_size if output.exists() else 0
        if size <= 0:
            raise DownloadError("Downloaded file is empty.")
        if size > self.settings.max_download_bytes:
            output.unlink(missing_ok=True)
            raise DownloadError(
                f"Downloaded file exceeded the {self.settings.max_download_mb} MB limit."
            )

        return DownloadResult(path=output, title=title, size_bytes=size)

    async def resolve_media_url(self, url: str, session: StoredSession) -> tuple[str, str]:
        if self._looks_like_direct_media(url):
            return url, self._title_from_url(url)

        async with httpx.AsyncClient(
            cookies=session.cookies,
            follow_redirects=True,
            timeout=httpx.Timeout(self.settings.request_timeout_seconds),
            headers=self._browser_headers(),
        ) as client:
            response = await self._request_with_retries(client, "GET", url)

        content_type = response.headers.get("content-type", "").lower()
        if any(kind in content_type for kind in ("video/", "mpegurl", "octet-stream")):
            return str(response.url), self._title_from_url(str(response.url))

        body = response.text
        if "widevine" in body.lower() or ".mpd" in body.lower():
            raise DownloadError("Encrypted DRM streams are not supported.")

        candidates = self._extract_media_candidates(body, str(response.url))
        if not candidates:
            raise DownloadError(
                "No downloadable media stream was found. Send a PWThor lecture page or direct PWThor .m3u8/.mp4 link."
            )

        return candidates[0], self._extract_title(body, url)

    async def _run_ffmpeg(
        self,
        media_url: str,
        output: Path,
        session: StoredSession,
        cancel_event: asyncio.Event,
        progress: ProgressCallback,
    ) -> None:
        cookie_header = self._cookie_header(session.cookies)
        header_lines = [
            "User-Agent: Mozilla/5.0",
            f"Referer: {self.settings.pwthor_base_url}/",
        ]
        if cookie_header:
            header_lines.append(f"Cookie: {cookie_header}")

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-headers",
            "\r\n".join(header_lines) + "\r\n",
            "-i",
            media_url,
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ]

        started = time.monotonic()
        last_progress = 0.0
        stderr_tail: list[str] = []

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        async def drain_stderr() -> None:
            assert process.stderr is not None
            async for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    stderr_tail.append(line)
                    del stderr_tail[:-8]

        stderr_task = asyncio.create_task(drain_stderr())

        try:
            while True:
                if cancel_event.is_set():
                    process.terminate()
                    raise DownloadError("Download cancelled.")

                if process.returncode is not None:
                    break

                elapsed = time.monotonic() - started
                if elapsed > self.settings.download_timeout_seconds:
                    process.terminate()
                    raise DownloadError("Download timed out.")

                current_size = output.stat().st_size if output.exists() else 0
                if current_size > self.settings.max_download_bytes:
                    process.terminate()
                    raise DownloadError(
                        f"Download exceeded the {self.settings.max_download_mb} MB limit."
                    )

                if time.monotonic() - last_progress >= self.settings.progress_update_seconds:
                    last_progress = time.monotonic()
                    await progress(f"Downloading... {current_size / 1024 / 1024:.1f} MB saved.")

                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except asyncio.TimeoutError:
                    continue

            await process.wait()
            if process.returncode != 0:
                detail = stderr_tail[-1] if stderr_tail else "ffmpeg failed"
                raise DownloadError(detail)
        finally:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()

    async def _request_with_retries(
        self, client: httpx.AsyncClient, method: str, url: str, **kwargs: object
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.settings.max_retries + 1):
            try:
                response = await client.request(method, url, **kwargs)
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError("server error", request=response.request, response=response)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt == self.settings.max_retries:
                    break
                await asyncio.sleep(min(2**attempt, 8))
        raise DownloadError(f"PWThor request failed: {last_error}") from last_error

    def _extract_media_candidates(self, body: str, base_url: str) -> list[str]:
        decoded = html.unescape(body).replace("\\/", "/")
        decoded = unquote(decoded)
        patterns = [
            r"https?://[^\"'<>\s]+?\.(?:m3u8|mp4|mkv|webm)(?:\?[^\"'<>\s]*)?",
            r"(?<!:)//[^\"'<>\s]+?\.(?:m3u8|mp4|mkv|webm)(?:\?[^\"'<>\s]*)?",
            r"/[^\"'<>\s]+?\.(?:m3u8|mp4|mkv|webm)(?:\?[^\"'<>\s]*)?",
        ]
        found: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, decoded, flags=re.IGNORECASE):
                candidate = match if match.startswith("http") else urljoin(base_url, match)
                if self._is_allowed_media_url(candidate) and candidate not in found:
                    found.append(candidate)

        found.sort(key=lambda item: (not item.lower().split("?")[0].endswith(".m3u8"), len(item)))
        return found

    def _is_allowed_media_url(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.scheme != "https":
            return False
        for allowed_host in self.settings.allowed_media_host_set:
            if host == allowed_host or host.endswith(f".{allowed_host}"):
                return True
        return False

    def _looks_like_direct_media(self, url: str) -> bool:
        return urlparse(url).path.lower().endswith((".m3u8", ".mp4", ".mkv", ".webm"))

    def _title_from_url(self, url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        name = Path(path).stem or "pwthor-video"
        return sanitize_filename(name)

    def _extract_title(self, body: str, fallback_url: str) -> str:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            return sanitize_filename(re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip())
        return self._title_from_url(fallback_url)

    def _browser_headers(self) -> dict[str, str]:
        return {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "user-agent": "Mozilla/5.0",
            "referer": f"{self.settings.pwthor_base_url}/",
        }

    def _cookie_header(self, cookies: dict[str, str]) -> str:
        return "; ".join(f"{key}={value}" for key, value in cookies.items())
