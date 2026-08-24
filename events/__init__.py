"""Structured internal event contracts and in-process bus."""

from events.bus import EventBus
from events.contracts import Event, EventType

__all__ = ["Event", "EventBus", "EventType"]
