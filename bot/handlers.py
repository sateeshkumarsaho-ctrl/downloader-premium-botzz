from __future__ import annotations

import asyncio
import time
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message

from app.downloader import DownloadError, PWThorDownloader
from app.pwthor_client import PWThorAuthError, PWThorClient, PendingLogin
from app.queue import DownloadJob, DownloadQueueManager
from config.settings import Settings
from utils.logger import get_logger
from utils.rate_limit import RateLimiter
from utils.security import normalize_phone
from utils.session_store import SessionStore
from utils.validators import URLValidationError, is_pwthor_url, validate_public_https_url


class ConversationState:
    AWAITING_PHONE = "awaiting_phone"
    AWAITING_OTP = "awaiting_otp"
    READY = "ready"


def register_handlers(app: Client, settings: Settings, store: SessionStore) -> None:
    logger = get_logger(__name__)
    pwthor = PWThorClient(settings)
    downloader = PWThorDownloader(settings)
    rate_limiter = RateLimiter(settings.rate_limit_messages, settings.rate_limit_window_seconds)
    global_downloads = asyncio.Semaphore(settings.max_global_downloads)
    states: dict[int, str] = {}
    pending_logins: dict[int, tuple[PendingLogin, float]] = {}
    pending_urls: dict[int, str] = {}

    async def admin_log(text: str) -> None:
        if not settings.admin_chat_id:
            return
        try:
            await app.send_message(settings.admin_chat_id, text[:3900])
        except RPCError:
            logger.exception("failed to send admin log")

    async def edit_status(message: Message, text: str) -> None:
        try:
            await message.edit_text(text)
        except FloodWait as exc:
            await asyncio.sleep(exc.value)
        except RPCError:
            pass

    async def process_download(job: DownloadJob) -> None:
        status = await app.send_message(job.chat_id, "Queued. Starting your download now.")
        temp_path: Path | None = None
        try:
            session = await store.load(job.user_id)
            if is_pwthor_url(job.url, settings.pwthor_base_url) and not session:
                await edit_status(status, "This PWThor link needs login. Send /start and try again.")
                return

            last_update = 0.0

            async def progress(text: str) -> None:
                nonlocal last_update
                now = time.monotonic()
                if now - last_update >= settings.progress_update_seconds:
                    last_update = now
                    await edit_status(status, text)

            async with global_downloads:
                result = await downloader.download(job.user_id, job.url, session, job.cancel_event, progress)
            temp_path = result.path
            if session:
                await store.touch(job.user_id)
            await edit_status(status, "Uploading to Telegram...")

            def upload_progress(current: int, total: int) -> None:
                nonlocal last_update
                now = time.monotonic()
                if now - last_update >= settings.progress_update_seconds:
                    last_update = now
                    asyncio.create_task(
                        edit_status(status, f"Uploading... {current * 100 / max(total, 1):.0f}%")
                    )

            await app.send_document(
                chat_id=job.chat_id,
                document=str(result.path),
                caption=f"{result.title}\n{result.size_bytes / 1024 / 1024:.1f} MB",
                progress=upload_progress,
            )
            await edit_status(status, "Done. Temporary file deleted.")
            await admin_log(f"Download completed for user {job.user_id}: {result.title}")
        except DownloadError as exc:
            await edit_status(status, f"Download failed: {exc}")
            await admin_log(f"Download failed for user {job.user_id}: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected download failure")
            await edit_status(status, "Download failed because of an internal error.")
            await admin_log(f"Unexpected download error for user {job.user_id}: {exc}")
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
                parent = temp_path.parent
                try:
                    parent.rmdir()
                except OSError:
                    pass

    queue_manager = DownloadQueueManager(settings.per_user_queue_size, process_download)

    async def enqueue_url(message: Message, url: str) -> None:
        try:
            position = await queue_manager.enqueue(
                DownloadJob(user_id=message.from_user.id, chat_id=message.chat.id, url=url)
            )
            await message.reply_text(f"Added to your queue. Position: {position}.")
        except asyncio.QueueFull:
            await message.reply_text("Your queue is full. Wait for a download to finish or use /cancel.")

    async def check_rate(message: Message) -> bool:
        user_id = message.from_user.id if message.from_user else message.chat.id
        if rate_limiter.allow(user_id):
            return True
        await message.reply_text("Too many requests. Please wait a moment and try again.")
        return False

    @app.on_message(filters.command("start") & filters.private)
    async def start(_: Client, message: Message) -> None:
        if not await check_rate(message):
            return
        user_id = message.from_user.id
        if await store.load(user_id):
            states[user_id] = ConversationState.READY
            await message.reply_text(
                "You are logged in. Send any supported HTTPS media page or direct video link."
            )
            return
        states.pop(user_id, None)
        await message.reply_text(
            "Send a video page or direct media link first. If a PWThor link needs login, I will ask for your phone and OTP."
        )

    @app.on_message(filters.command("logout") & filters.private)
    async def logout(_: Client, message: Message) -> None:
        if not await check_rate(message):
            return
        user_id = message.from_user.id
        await queue_manager.cancel_user(user_id)
        await store.delete(user_id)
        pending_logins.pop(user_id, None)
        pending_urls.pop(user_id, None)
        states.pop(user_id, None)
        await message.reply_text("Logged out and removed your saved PWThor session.")

    @app.on_message(filters.command("cancel") & filters.private)
    async def cancel(_: Client, message: Message) -> None:
        if not await check_rate(message):
            return
        user_id = message.from_user.id
        cancelled = await queue_manager.cancel_user(user_id)
        if states.get(user_id) == ConversationState.AWAITING_OTP:
            pending_logins.pop(user_id, None)
            pending_urls.pop(user_id, None)
            states.pop(user_id, None)
            cancelled += 1
        await message.reply_text("Cancelled active and queued work." if cancelled else "No active download to cancel.")

    @app.on_message(filters.command("status") & filters.private)
    async def status(_: Client, message: Message) -> None:
        if not await check_rate(message):
            return
        user_id = message.from_user.id
        session = await store.load(user_id)
        await message.reply_text("Logged in." if session else "Not logged in. Send /start.")

    @app.on_message(filters.text & filters.private)
    async def text_handler(_: Client, message: Message) -> None:
        if not await check_rate(message):
            return
        if not message.text or not message.from_user:
            return

        user_id = message.from_user.id
        text = message.text.strip()
        state = states.get(user_id)

        if state == ConversationState.AWAITING_OTP:
            pending = pending_logins.get(user_id)
            if not pending:
                states[user_id] = ConversationState.AWAITING_PHONE
                await message.reply_text("Your OTP request expired. Send your phone number again.")
                return
            if time.time() - pending[1] > 600:
                pending_logins.pop(user_id, None)
                states[user_id] = ConversationState.AWAITING_PHONE
                await message.reply_text("Your OTP request expired. Send your phone number again.")
                return
            try:
                stored_session = await pwthor.verify_otp(pending[0], text)
                await store.save(user_id, stored_session)
                pending_logins.pop(user_id, None)
                states[user_id] = ConversationState.READY
                queued_url = pending_urls.pop(user_id, None)
                await message.reply_text("Login successful.")
                if queued_url:
                    await enqueue_url(message, queued_url)
                await admin_log(f"PWThor login successful for Telegram user {user_id}")
            except PWThorAuthError as exc:
                await message.reply_text(f"OTP verification failed: {exc}")
            return

        if state == ConversationState.AWAITING_PHONE:
            try:
                phone = normalize_phone(text)
                pending = await pwthor.request_otp(phone)
                pending_logins[user_id] = (pending, time.time())
                states[user_id] = ConversationState.AWAITING_OTP
                await message.reply_text("OTP sent. Reply with the OTP from PWThor.")
            except (ValueError, PWThorAuthError) as exc:
                await message.reply_text(f"Could not start login: {exc}")
            return

        try:
            url = validate_public_https_url(text, settings.allowed_source_host_set)
        except URLValidationError as exc:
            await message.reply_text(f"Invalid URL: {exc}")
            return

        if is_pwthor_url(url, settings.pwthor_base_url) and not await store.load(user_id):
            pending_urls[user_id] = url
            states[user_id] = ConversationState.AWAITING_PHONE
            await message.reply_text(
                "This PWThor link may need login. Send your 10-digit PWThor phone number."
            )
            return

        await enqueue_url(message, url)
