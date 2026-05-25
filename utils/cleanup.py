from __future__ import annotations

import asyncio
import time

from config.settings import Settings
from utils.logger import get_logger
from utils.session_store import SessionStore


def ensure_runtime_dirs(settings: Settings) -> None:
    settings.downloads_dir.mkdir(parents=True, exist_ok=True)
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)


async def cleanup_loop(settings: Settings, store: SessionStore, stop_event: asyncio.Event) -> None:
    logger = get_logger(__name__)
    while not stop_event.is_set():
        try:
            await cleanup_downloads(settings)
            await store.cleanup_expired()
        except Exception:
            logger.exception("cleanup task failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.cleanup_interval_seconds)
        except asyncio.TimeoutError:
            continue


async def cleanup_downloads(settings: Settings) -> None:
    cutoff = time.time() - max(settings.download_timeout_seconds, settings.cleanup_interval_seconds)

    def _cleanup() -> None:
        if not settings.downloads_dir.exists():
            return
        for path in settings.downloads_dir.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        for directory in sorted(settings.downloads_dir.rglob("*"), reverse=True):
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass

    await asyncio.to_thread(_cleanup)
