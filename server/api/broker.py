"""In-process pub/sub for the live feed, one channel per team.

Good enough for a single API process. With several processes, switch to Postgres LISTEN/NOTIFY.
"""

import asyncio
from collections import defaultdict

QUEUE_SIZE = 1000


class Broker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, team: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers[team].add(queue)
        return queue

    def unsubscribe(self, team: str, queue: asyncio.Queue) -> None:
        self._subscribers[team].discard(queue)

    def publish(self, team: str, item: dict) -> None:
        for queue in list(self._subscribers[team]):
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                # A slow viewer misses events instead of slowing down ingestion
                pass

    def subscriber_count(self, team: str) -> int:
        return len(self._subscribers[team])
