from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..config import AlphaLabSettings
from .models import DirectionFlipEvent, RealtimeWatch, SignalRecord


@runtime_checkable
class WatchRepository(Protocol):
    def save(self, watch: RealtimeWatch) -> RealtimeWatch: ...

    def get(self, watch_id: str) -> RealtimeWatch | None: ...

    def list(self) -> list[RealtimeWatch]: ...


RealtimeWatchRepository = WatchRepository


class InMemoryRealtimeWatchRepository:
    def __init__(self) -> None:
        self._watches: dict[str, RealtimeWatch] = {}

    def save(self, watch: RealtimeWatch) -> RealtimeWatch:
        self._watches[watch.id] = watch
        return watch

    def get(self, watch_id: str) -> RealtimeWatch | None:
        return self._watches.get(watch_id)

    def list(self) -> list[RealtimeWatch]:
        return list(self._watches.values())

    def delete(self, watch_id: str) -> bool:
        return self._watches.pop(watch_id, None) is not None


class FileRealtimeWatchRepository:
    """Durable watch state kept under AlphaLabSettings.artifact_root."""

    def __init__(
        self,
        settings: AlphaLabSettings | None = None,
        *,
        root: str | Path | None = None,
    ) -> None:
        self.settings = settings or AlphaLabSettings()
        self.root = (
            Path(root)
            if root is not None
            else self.settings.artifact_root / "realtime" / "watches"
        )

    def _path(self, watch_id: str) -> Path:
        digest = hashlib.sha256(watch_id.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def save(self, watch: RealtimeWatch) -> RealtimeWatch:
        path = self._path(watch.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(watch.to_dict(), handle, ensure_ascii=False, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return watch

    def get(self, watch_id: str) -> RealtimeWatch | None:
        path = self._path(watch_id)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        watch = RealtimeWatch.from_dict(payload)
        if watch.id != watch_id:
            raise ValueError(f"realtime watch id mismatch in {path}")
        return watch

    def list(self) -> list[RealtimeWatch]:
        if not self.root.exists():
            return []
        return [
            RealtimeWatch.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.root.glob("*.json"))
        ]

    def delete(self, watch_id: str) -> bool:
        path = self._path(watch_id)
        if not path.exists():
            return False
        path.unlink()
        return True


@runtime_checkable
class SignalStore(Protocol):
    def get(self, signal_key: str) -> SignalRecord | None: ...

    def save_if_absent(self, signal: SignalRecord) -> tuple[SignalRecord, bool]: ...


class InMemorySignalStore:
    def __init__(self) -> None:
        self._signals: dict[str, SignalRecord] = {}

    def get(self, signal_key: str) -> SignalRecord | None:
        return self._signals.get(signal_key)

    def __len__(self) -> int:
        return len(self._signals)

    def save_if_absent(self, signal: SignalRecord) -> tuple[SignalRecord, bool]:
        existing = self._signals.get(signal.signal_key)
        if existing is not None:
            return existing, False
        self._signals[signal.signal_key] = signal
        return signal, True

    def list(self, *, limit: int | None = None) -> list[SignalRecord]:
        records = sorted(
            self._signals.values(),
            key=lambda item: (item.generated_at, item.signal_key),
            reverse=True,
        )
        return records[:limit] if limit is not None else records


class FileSignalStore:
    """File-backed idempotency store for scheduler retries and restarts."""

    def __init__(
        self,
        settings: AlphaLabSettings | None = None,
        *,
        root: str | Path | None = None,
    ) -> None:
        self.settings = settings or AlphaLabSettings()
        self.root = (
            Path(root)
            if root is not None
            else self.settings.artifact_root / "realtime" / "signals"
        )
        self._lock = __import__("threading").RLock()

    def _path(self, signal_key: str) -> Path:
        digest = hashlib.sha256(signal_key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def get(self, signal_key: str) -> SignalRecord | None:
        path = self._path(signal_key)
        if not path.exists():
            return None
        record = SignalRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if record.signal_key != signal_key:
            raise ValueError(f"signal key mismatch in {path}")
        return record

    def save_if_absent(self, signal: SignalRecord) -> tuple[SignalRecord, bool]:
        with self._lock:
            existing = self.get(signal.signal_key)
            if existing is not None:
                return existing, False
            path = self._path(signal.signal_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(signal.to_dict(), handle, ensure_ascii=False, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary_name, path)
                except FileExistsError:
                    existing = self.get(signal.signal_key)
                    if existing is None:
                        raise
                    return existing, False
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
            return signal, True

    def list(self, *, limit: int | None = None) -> list[SignalRecord]:
        if not self.root.exists():
            return []
        records = [
            SignalRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in self.root.glob("*.json")
        ]
        records.sort(
            key=lambda item: (item.generated_at, item.signal_key),
            reverse=True,
        )
        return records[:limit] if limit is not None else records


class InMemoryEventPublisher:
    def __init__(self) -> None:
        self.events: list[DirectionFlipEvent] = []

    def publish(self, event: DirectionFlipEvent) -> None:
        self.events.append(event)
