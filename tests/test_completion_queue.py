"""Admission, FIFO order, timeout, cancellation, and cross-thread release."""

import asyncio
import unittest

from vectorizer.completion_queue import CompletionQueue, CompletionQueueFull


class CompletionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_requests_wait_in_fifo_order(self):
        queue = CompletionQueue(max_waiting=3)
        first = await queue.acquire()
        order = []

        async def request(number):
            lease = await queue.acquire()
            order.append(number)
            lease.release()

        tasks = [asyncio.create_task(request(number)) for number in range(3)]
        await asyncio.sleep(0)
        self.assertEqual(order, [])
        first.release()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        self.assertEqual(order, [0, 1, 2])

    async def test_queue_full_and_cancelled_waiter_return_capacity(self):
        queue = CompletionQueue(max_waiting=1)
        first = await queue.acquire()
        waiting = asyncio.create_task(queue.acquire())
        await asyncio.sleep(0)
        with self.assertRaises(CompletionQueueFull):
            await queue.acquire()
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        replacement = asyncio.create_task(queue.acquire())
        first.release()
        lease = await asyncio.wait_for(replacement, timeout=2)
        lease.release()

    async def test_timeout_returns_the_waiting_position(self):
        queue = CompletionQueue(max_waiting=1, wait_timeout=0.02)
        first = await queue.acquire()
        for _ in range(2):
            with self.assertRaises(TimeoutError):
                await queue.acquire()
        first.release()
        lease = await queue.acquire()
        lease.release()

    async def test_worker_thread_release_is_idempotent(self):
        queue = CompletionQueue(max_waiting=0)
        first = await queue.acquire()
        await asyncio.to_thread(first.release)
        first.release()
        second = await queue.acquire()
        second.release()

    async def test_invalid_limits_are_rejected(self):
        for options in ({"max_waiting": -1}, {"wait_timeout": 0}):
            with self.assertRaises(ValueError):
                CompletionQueue(**options)
