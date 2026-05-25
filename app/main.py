from __future__ import annotations

import asyncio
import sys

from pyrogram import Client, idle

from bot.handlers import register_handlers
from config.settings import Settings
from utils.cleanup import cleanup_loop, ensure_runtime_dirs
from utils.logger import configure_logging, get_logger
from utils.session_store import SessionStore


async def main() -> None:
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("This bot is supported on Python 3.11 only.")

    settings = Settings()
    configure_logging(settings.log_level)
    ensure_runtime_dirs(settings)

    logger = get_logger(__name__)
    store = SessionStore(settings)

    app = Client(
        name="pwthor_downloader_bot",
        api_id=settings.api_id,
        api_hash=settings.api_hash,
        bot_token=settings.bot_token,
        in_memory=True,
        workdir=str(settings.sessions_dir),
    )

    register_handlers(app, settings, store)

    stop_event = asyncio.Event()

    async with app:
        cleanup_task = asyncio.create_task(cleanup_loop(settings, store, stop_event))
        logger.info("bot started")
        await idle()
        stop_event.set()
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
