"""Non-blocking fan-out for advisory generation events."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(eq=False)
class Subscription:
    owner_id: str
    queue: asyncio.Queue[dict[str, Any]]

    async def receive(self) -> dict[str, Any]:
        return await self.queue.get()


class EventBroker:
    """A slow subscriber loses old advisory events; producers never wait."""

    def __init__(self, pending_cap: int = 32) -> None:
        self.pending_cap = max(1, pending_cap)
        self._subscribers: dict[str, set[Subscription]] = {}

    def subscribe(self, owner_id: str) -> Subscription:
        subscription = Subscription(owner_id, asyncio.Queue(self.pending_cap))
        self._subscribers.setdefault(owner_id, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        subscribers = self._subscribers.get(subscription.owner_id)
        if subscribers is None:
            return
        subscribers.discard(subscription)
        if not subscribers:
            self._subscribers.pop(subscription.owner_id, None)

    def publish(self, owner_id: str, event: dict[str, Any]) -> None:
        for subscription in tuple(self._subscribers.get(owner_id, ())):
            if subscription.queue.full():
                subscription.queue.get_nowait()
            subscription.queue.put_nowait(event)
