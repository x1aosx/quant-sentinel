"""Closed-bar realtime analysis with persistent watches and idempotent signals."""

from .analyzer import (
    RealtimeAnalyzer,
    bar_idempotency_key,
    signal_idempotency_key,
    watch_idempotency_key,
)
from .models import DirectionFlipEvent, RealtimeWatch, SignalRecord
from .stores import (
    FileRealtimeWatchRepository,
    FileSignalStore,
    InMemoryEventPublisher,
    InMemoryRealtimeWatchRepository,
    InMemorySignalStore,
    RealtimeWatchRepository,
    SignalStore,
    WatchRepository,
)

__all__ = [
    "DirectionFlipEvent",
    "FileRealtimeWatchRepository",
    "FileSignalStore",
    "InMemoryEventPublisher",
    "InMemoryRealtimeWatchRepository",
    "InMemorySignalStore",
    "RealtimeAnalyzer",
    "RealtimeWatch",
    "RealtimeWatchRepository",
    "SignalRecord",
    "SignalStore",
    "WatchRepository",
    "bar_idempotency_key",
    "signal_idempotency_key",
    "watch_idempotency_key",
]
