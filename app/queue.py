from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable


@dataclass(slots=True)
class DownloadJob:
    user_id: int
    chat_id: int
    url: str
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


JobHandler = Callable[[DownloadJob], Awaitable[None]]


class DownloadQueueManager:
    def __init__(self, per_user_queue_size: int, handler: JobHandler) -> None:
        self.per_user_queue_size = per_user_queue_size
        self.handler = handler
        self._queues: dict[int, asyncio.Queue[DownloadJob]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}
        self._current: dict[int, DownloadJob] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, job: DownloadJob) -> int:
        async with self._lock:
            queue = self._queues.setdefault(
                job.user_id, asyncio.Queue(maxsize=self.per_user_queue_size)
            )
            if queue.full():
                raise asyncio.QueueFull
            queue.put_nowait(job)
            if job.user_id not in self._workers or self._workers[job.user_id].done():
                self._workers[job.user_id] = asyncio.create_task(self._worker(job.user_id))
            return queue.qsize()

    async def cancel_user(self, user_id: int) -> int:
        cancelled = 0
        async with self._lock:
            current = self._current.get(user_id)
            if current:
                current.cancel_event.set()
                cancelled += 1
            queue = self._queues.get(user_id)
            if queue:
                while not queue.empty():
                    queued = queue.get_nowait()
                    queued.cancel_event.set()
                    queue.task_done()
                    cancelled += 1
        return cancelled

    async def _worker(self, user_id: int) -> None:
        queue = self._queues[user_id]
        while True:
            try:
                job = await asyncio.wait_for(queue.get(), timeout=300)
            except asyncio.TimeoutError:
                if queue.empty():
                    self._queues.pop(user_id, None)
                    self._workers.pop(user_id, None)
                    return
                continue

            self._current[user_id] = job
            try:
                if not job.cancel_event.is_set():
                    await self.handler(job)
            finally:
                self._current.pop(user_id, None)
                queue.task_done()
