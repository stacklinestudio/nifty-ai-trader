"""Synchronous deterministic event bus with duplicate-id protection."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from events.contracts import Event, EventType

Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self, audit_sink: Callable[[Event], None] | None = None) -> None:
        self._subscribers: dict[EventType, list[Subscriber]] = defaultdict(list)
        self._seen: set[str] = set()
        self._audit_sink = audit_sink

    def subscribe(self, event_type: EventType, subscriber: Subscriber) -> None:
        self._subscribers[event_type].append(subscriber)

    def publish(self, event: Event) -> bool:
        """Returns false for an already processed event ID."""
        if event.event_id in self._seen:
            return False
        self._seen.add(event.event_id)
        if self._audit_sink:
            self._audit_sink(event)
        for subscriber in self._subscribers[event.event_type]:
            subscriber(event)
        return True
