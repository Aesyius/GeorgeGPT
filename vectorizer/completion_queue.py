"""Bounded, cancellation-safe admission to one local model generation slot."""

import asyncio
import threading


class CompletionQueueFull(RuntimeError):
    """The active request and all waiting positions are occupied."""


class CompletionLease:
    """Release exactly once, including from Starlette's background worker thread."""

    def __init__(self, queue: "CompletionQueue"):
        self._queue = queue
        self._guard = threading.Lock()
        self._released = False

    def release(self) -> None:
        with self._guard:
            if self._released:
                return
            self._released = True
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is self._queue.loop:
            self._queue.release()
        elif not self._queue.loop.is_closed():
            self._queue.loop.call_soon_threadsafe(self._queue.release)


class CompletionQueue:
    """FIFO lock with bounded waiting, rather than immediate overlap rejection.

    Admission and lock state are owned by the application's asyncio loop.
    Waiting never occupies a worker thread, and cancellation returns capacity.
    """

    def __init__(self, max_waiting: int = 4, wait_timeout: float = 90):
        if max_waiting < 0 or wait_timeout <= 0:
            raise ValueError("max_waiting must be nonnegative and wait_timeout positive")
        self.loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._capacity = max_waiting + 1
        self._admitted = 0
        self._wait_timeout = wait_timeout

    async def acquire(self) -> CompletionLease:
        if self._admitted >= self._capacity:
            raise CompletionQueueFull("Completion queue is full")
        self._admitted += 1
        acquired = False
        try:
            async with asyncio.timeout(self._wait_timeout):
                await self._lock.acquire()
                acquired = True
        except BaseException:
            if acquired:
                self._lock.release()
            self._admitted -= 1
            raise
        return CompletionLease(self)

    def release(self) -> None:
        """Called on the owner loop by a lease, not by request worker threads."""
        self._admitted -= 1
        self._lock.release()
