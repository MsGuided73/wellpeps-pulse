"""Bounded concurrency for the per-mention pipeline batches.

``triage_batch`` and ``draft_batch`` take ``concurrency`` (default 1: strictly
one mention after another, as always). Above 1, mentions in the same thread
still run one at a time and in order (the stop rules and "one reply per thread"
read what the earlier mention wrote); different threads run side by side, at
most ``concurrency`` at once. Used by scripts/seed_demo.py --claude; the
heartbeat keeps the default.
"""

import asyncio
from typing import Awaitable, Callable, Iterable, TypeVar

T = TypeVar("T")


async def run_by_thread(items: Iterable[T], thread_of: Callable[[T], str],
                        worker: Callable[[T], Awaitable[bool | None]], concurrency: int) -> None:
    """``await worker(item)`` for every item, grouped by ``thread_of(item)``
    ("" = a thread of its own). A worker returning False stops new work."""
    groups: dict[str, list[T]] = {}
    for n, item in enumerate(items):
        groups.setdefault(thread_of(item) or f"\0solo-{n}", []).append(item)
    gate = asyncio.Semaphore(max(1, int(concurrency)))
    stopped = False

    async def run(group: list[T]) -> None:
        nonlocal stopped
        for item in group:
            async with gate:
                if stopped:
                    return
                if await worker(item) is False:
                    stopped = True
                    return

    await asyncio.gather(*(run(group) for group in groups.values()))
