"""Bounded, cancellation-safe admission for the chat interface's generation slot."""

import asyncio
import threading


class CompletionQueueFull(RuntimeError):
    """The active request and all waiting positions are occupied."""


class CompletionLease:
    """Release exactly once, including from Starlette's background worker thread."""

    def __init__(self, queue: "CompletionQueue"):
        """Associate a held generation slot with an idempotent release guard.

        Construct leases through `CompletionQueue.acquire`; construction alone
        does not acquire or reserve a queue position.
        """
        self._queue = queue
        self._guard = threading.Lock()
        self._released = False

    def release(self) -> None:
        """Return this lease's slot once, scheduling onto the owning event loop.

        Safe to call repeatedly and from worker threads. If the owning loop is
        already closed, no callback is scheduled. Does not wait for model work
        to finish; callers release only when their generation/stream is done.
        """
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
        """Create a single-generation queue on the current running asyncio loop.

        `max_waiting` excludes the active generation; `wait_timeout` is the
        positive admission wait in seconds. Invalid limits raise `ValueError`;
        construction outside a running event loop raises `RuntimeError`.
        """
        if max_waiting < 0 or wait_timeout <= 0:
            raise ValueError("max_waiting must be nonnegative and wait_timeout positive")
        self.loop = asyncio.get_running_loop()
        self._lock = asyncio.Lock()
        self._capacity = max_waiting + 1
        self._admitted = 0
        self._wait_timeout = wait_timeout

    async def acquire(self) -> CompletionLease:
        """Wait FIFO for generation access and return its releasable lease.

        Raises `CompletionQueueFull` when active plus waiting capacity is full,
        or `TimeoutError` when the wait expires. Cancellation/timeout returns
        admission capacity automatically. A successful caller must release the
        returned lease, including when a streaming response fails or disconnects.
        """
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
