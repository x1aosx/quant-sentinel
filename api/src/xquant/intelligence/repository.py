from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .domain import (
    BriefType,
    InformationBrief,
    InformationStatus,
    MarketEvent,
    NotificationEvent,
    RawInformation,
    SourceType,
    Theme,
    compute_content_hash,
    ensure_utc,
    information_id,
    utc_iso,
)


@runtime_checkable
class IntelligenceRepository(Protocol):
    def initialize(self) -> None:
        ...

    def save_information(self, items: Sequence[RawInformation]) -> int:
        ...

    def list_information(
        self,
        *,
        limit: int = 100,
        source_type: SourceType | str | None = None,
        status: InformationStatus | str | None = None,
        available_before: datetime | None = None,
    ) -> list[RawInformation]:
        ...

    def save_events(self, events: Sequence[MarketEvent]) -> int:
        ...

    def list_events(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        available_before: datetime | None = None,
    ) -> list[MarketEvent]:
        ...

    def get_event(self, event_id: str) -> MarketEvent | None:
        ...

    def save_themes(self, themes: Sequence[Theme]) -> int:
        ...

    def list_themes(
        self,
        *,
        limit: int = 100,
        state: str | None = None,
    ) -> list[Theme]:
        ...

    def get_theme(self, key: str) -> Theme | None:
        ...

    def save_brief(self, brief: InformationBrief) -> None:
        ...

    def get_morning_brief(self) -> InformationBrief | None:
        ...

    def save_notifications(self, notifications: Sequence[NotificationEvent]) -> int:
        ...

    def list_notifications(
        self,
        *,
        limit: int = 100,
        severity: str | None = None,
    ) -> list[NotificationEvent]:
        ...

    def counts(self) -> dict[str, int]:
        ...


class SqliteIntelligenceRepository:
    """SQLite persistence using the path owned by the legacy database."""

    def __init__(self, database: Any) -> None:
        path = getattr(database, "path", None)
        if path is None:
            raise TypeError("legacy database must expose a sqlite path")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        connection = self._connect()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS intelligence_information (
                id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL UNIQUE,
                source TEXT NOT NULL,
                source_type TEXT NOT NULL,
                title TEXT NOT NULL,
                publish_time TEXT NOT NULL,
                fetch_time TEXT NOT NULL,
                process_time TEXT,
                available_time TEXT,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intelligence_information_available
                ON intelligence_information (available_time DESC);
            CREATE INDEX IF NOT EXISTS idx_intelligence_information_source_type
                ON intelligence_information (source_type, publish_time DESC);

            CREATE TABLE IF NOT EXISTS intelligence_events (
                id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                title TEXT NOT NULL,
                importance REAL NOT NULL,
                heat_score REAL NOT NULL,
                first_publish_time TEXT NOT NULL,
                available_time TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intelligence_events_available
                ON intelligence_events (available_time DESC);
            CREATE INDEX IF NOT EXISTS idx_intelligence_events_type
                ON intelligence_events (event_type, heat_score DESC);

            CREATE TABLE IF NOT EXISTS intelligence_themes (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                state TEXT NOT NULL,
                category TEXT NOT NULL,
                current_heat REAL NOT NULL,
                forward_heat REAL NOT NULL,
                crowding REAL NOT NULL,
                updated_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intelligence_themes_heat
                ON intelligence_themes (current_heat DESC, forward_heat DESC);

            CREATE TABLE IF NOT EXISTS intelligence_briefs (
                id TEXT PRIMARY KEY,
                brief_type TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                available_time TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intelligence_briefs_type
                ON intelligence_briefs (brief_type, generated_at DESC);

            CREATE TABLE IF NOT EXISTS intelligence_notifications (
                id TEXT PRIMARY KEY,
                dedup_key TEXT NOT NULL UNIQUE,
                severity TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_intelligence_notifications_created
                ON intelligence_notifications (created_at DESC);
            """
        )
        connection.commit()
        connection.close()

    def save_information(self, items: Sequence[RawInformation]) -> int:
        rows = []
        for item in items:
            record = _prepare_information(item)
            rows.append(
                {
                    "id": str(record.id),
                    "content_hash": record.content_hash,
                    "source": record.source,
                    "source_type": record.source_type.value,
                    "title": record.title,
                    "publish_time": utc_iso(record.publish_time),
                    "fetch_time": utc_iso(record.fetch_time),
                    "process_time": utc_iso(record.process_time),
                    "available_time": utc_iso(record.available_time),
                    "status": record.status.value,
                    "payload": _dump(record),
                }
            )
        if not rows:
            return 0
        connection = self._connect()
        with connection:
            connection.executemany(
                """
                INSERT INTO intelligence_information
                (
                    id, content_hash, source, source_type, title, publish_time,
                    fetch_time, process_time, available_time, status, payload
                )
                VALUES
                (
                    :id, :content_hash, :source, :source_type, :title, :publish_time,
                    :fetch_time, :process_time, :available_time, :status, :payload
                )
                ON CONFLICT(content_hash) DO UPDATE SET
                    id = excluded.id,
                    source = excluded.source,
                    source_type = excluded.source_type,
                    title = excluded.title,
                    publish_time = excluded.publish_time,
                    fetch_time = excluded.fetch_time,
                    process_time = excluded.process_time,
                    available_time = excluded.available_time,
                    status = excluded.status,
                    payload = excluded.payload
                """,
                rows,
            )
        connection.close()
        return len(rows)

    def list_information(
        self,
        *,
        limit: int = 100,
        source_type: SourceType | str | None = None,
        status: InformationStatus | str | None = None,
        available_before: datetime | None = None,
    ) -> list[RawInformation]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": _limit(limit)}
        if source_type is not None:
            clauses.append("source_type = :source_type")
            params["source_type"] = SourceType(source_type).value
        if status is not None:
            clauses.append("status = :status")
            params["status"] = InformationStatus(status).value
        before = ensure_utc(available_before, field_name="available_before")
        if before is not None:
            clauses.append("available_time <= :available_before")
            params["available_before"] = utc_iso(before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        rows = connection.execute(
            f"""
            SELECT payload
            FROM intelligence_information
            {where}
            ORDER BY publish_time DESC, id
            LIMIT :limit
            """,
            params,
        ).fetchall()
        connection.close()
        return [RawInformation.from_dict(_load(row["payload"])) for row in rows]

    def save_events(self, events: Sequence[MarketEvent]) -> int:
        rows = [
            {
                "id": str(event.id),
                "event_type": event.event_type.value,
                "title": event.title,
                "importance": event.importance,
                "heat_score": event.heat_score,
                "first_publish_time": utc_iso(event.first_publish_time),
                "available_time": utc_iso(event.last_update_time),
                "payload": _dump(event),
            }
            for event in events
        ]
        if not rows:
            return 0
        connection = self._connect()
        with connection:
            connection.executemany(
                """
                INSERT INTO intelligence_events
                (
                    id, event_type, title, importance, heat_score,
                    first_publish_time, available_time, payload
                )
                VALUES
                (
                    :id, :event_type, :title, :importance, :heat_score,
                    :first_publish_time, :available_time, :payload
                )
                ON CONFLICT(id) DO UPDATE SET
                    event_type = excluded.event_type,
                    title = excluded.title,
                    importance = excluded.importance,
                    heat_score = excluded.heat_score,
                    first_publish_time = excluded.first_publish_time,
                    available_time = excluded.available_time,
                    payload = excluded.payload
                """,
                rows,
            )
        connection.close()
        return len(rows)

    def list_events(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        available_before: datetime | None = None,
    ) -> list[MarketEvent]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": _limit(limit)}
        if event_type is not None:
            clauses.append("event_type = :event_type")
            params["event_type"] = str(event_type).upper()
        before = ensure_utc(available_before, field_name="available_before")
        if before is not None:
            clauses.append("available_time <= :available_before")
            params["available_before"] = utc_iso(before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        rows = connection.execute(
            f"""
            SELECT payload
            FROM intelligence_events
            {where}
            ORDER BY heat_score DESC, first_publish_time DESC, id
            LIMIT :limit
            """,
            params,
        ).fetchall()
        connection.close()
        return [MarketEvent.from_dict(_load(row["payload"])) for row in rows]

    def get_event(self, event_id: str) -> MarketEvent | None:
        connection = self._connect()
        row = connection.execute(
            "SELECT payload FROM intelligence_events WHERE id = ?",
            (str(event_id),),
        ).fetchone()
        connection.close()
        return MarketEvent.from_dict(_load(row["payload"])) if row else None

    def save_themes(self, themes: Sequence[Theme]) -> int:
        rows = [
            {
                "id": str(theme.id),
                "code": theme.code,
                "name": theme.name,
                "state": theme.state.value,
                "category": theme.category.value,
                "current_heat": theme.current_heat_score,
                "forward_heat": theme.forward_heat_score,
                "crowding": theme.crowding_score,
                "updated_at": utc_iso(theme.updated_at),
                "payload": _dump(theme),
            }
            for theme in themes
        ]
        if not rows:
            return 0
        connection = self._connect()
        with connection:
            connection.executemany(
                """
                INSERT INTO intelligence_themes
                (
                    id, code, name, state, category, current_heat,
                    forward_heat, crowding, updated_at, payload
                )
                VALUES
                (
                    :id, :code, :name, :state, :category, :current_heat,
                    :forward_heat, :crowding, :updated_at, :payload
                )
                ON CONFLICT(id) DO UPDATE SET
                    code = excluded.code,
                    name = excluded.name,
                    state = excluded.state,
                    category = excluded.category,
                    current_heat = excluded.current_heat,
                    forward_heat = excluded.forward_heat,
                    crowding = excluded.crowding,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                rows,
            )
        connection.close()
        return len(rows)

    def list_themes(
        self,
        *,
        limit: int = 100,
        state: str | None = None,
    ) -> list[Theme]:
        params: dict[str, Any] = {"limit": _limit(limit)}
        where = ""
        if state is not None:
            where = "WHERE state = :state"
            params["state"] = str(state).upper()
        connection = self._connect()
        rows = connection.execute(
            f"""
            SELECT payload
            FROM intelligence_themes
            {where}
            ORDER BY current_heat DESC, forward_heat DESC, name
            LIMIT :limit
            """,
            params,
        ).fetchall()
        connection.close()
        return [Theme.from_dict(_load(row["payload"])) for row in rows]

    def get_theme(self, key: str) -> Theme | None:
        connection = self._connect()
        row = connection.execute(
            """
            SELECT payload
            FROM intelligence_themes
            WHERE id = ? OR code = ? OR name = ?
            LIMIT 1
            """,
            (str(key), str(key), str(key)),
        ).fetchone()
        connection.close()
        return Theme.from_dict(_load(row["payload"])) if row else None

    def save_brief(self, brief: InformationBrief) -> None:
        connection = self._connect()
        with connection:
            connection.execute(
                """
                INSERT INTO intelligence_briefs
                (
                    id, brief_type, generated_at, available_time, payload
                )
                VALUES
                (
                    :id, :brief_type, :generated_at, :available_time, :payload
                )
                ON CONFLICT(id) DO UPDATE SET
                    brief_type = excluded.brief_type,
                    generated_at = excluded.generated_at,
                    available_time = excluded.available_time,
                    payload = excluded.payload
                """,
                {
                    "id": str(brief.id),
                    "brief_type": brief.brief_type.value,
                    "generated_at": utc_iso(brief.generated_at),
                    "available_time": utc_iso(brief.available_time),
                    "payload": _dump(brief),
                },
            )
        connection.close()

    def get_morning_brief(self) -> InformationBrief | None:
        connection = self._connect()
        row = connection.execute(
            """
            SELECT payload
            FROM intelligence_briefs
            WHERE brief_type = ?
            ORDER BY available_time DESC, generated_at DESC, id
            LIMIT 1
            """,
            (BriefType.MORNING.value,),
        ).fetchone()
        connection.close()
        return InformationBrief.from_dict(_load(row["payload"])) if row else None

    def save_notifications(self, notifications: Sequence[NotificationEvent]) -> int:
        rows = [
            {
                "id": str(notification.id),
                "dedup_key": notification.dedup_key,
                "severity": notification.severity.value,
                "created_at": utc_iso(notification.created_at),
                "payload": _dump(notification),
            }
            for notification in notifications
        ]
        if not rows:
            return 0
        connection = self._connect()
        with connection:
            connection.executemany(
                """
                INSERT INTO intelligence_notifications
                (
                    id, dedup_key, severity, created_at, payload
                )
                VALUES
                (
                    :id, :dedup_key, :severity, :created_at, :payload
                )
                ON CONFLICT(dedup_key) DO UPDATE SET
                    id = excluded.id,
                    severity = excluded.severity,
                    created_at = excluded.created_at,
                    payload = excluded.payload
                """,
                rows,
            )
        connection.close()
        return len(rows)

    def list_notifications(
        self,
        *,
        limit: int = 100,
        severity: str | None = None,
    ) -> list[NotificationEvent]:
        params: dict[str, Any] = {"limit": _limit(limit)}
        where = ""
        if severity is not None:
            where = "WHERE severity = :severity"
            params["severity"] = str(severity).upper()
        connection = self._connect()
        rows = connection.execute(
            f"""
            SELECT payload
            FROM intelligence_notifications
            {where}
            ORDER BY created_at DESC, id
            LIMIT :limit
            """,
            params,
        ).fetchall()
        connection.close()
        return [
            NotificationEvent.from_dict(_load(row["payload"]))
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        tables = {
            "information": "intelligence_information",
            "events": "intelligence_events",
            "themes": "intelligence_themes",
            "briefs": "intelligence_briefs",
            "notifications": "intelligence_notifications",
        }
        connection = self._connect()
        result = {
            name: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for name, table in tables.items()
        }
        connection.close()
        return result


class PostgresIntelligenceRepository:
    """PostgreSQL persistence over the existing ``db.postgres`` store."""

    def __init__(self, database: Any, *, schema: str = "intelligence") -> None:
        self.database = database
        self.postgres = getattr(database, "postgres", None)
        if self.postgres is None:
            raise TypeError("database must expose a postgres store")
        self.schema = _identifier(schema)
        self.initialize()

    def initialize(self) -> None:
        self.postgres.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
        for statement in _postgres_schema(self.schema):
            self.postgres.execute(statement)

    def save_information(self, items: Sequence[RawInformation]) -> int:
        rows = []
        for item in items:
            record = _prepare_information(item)
            rows.append(
                {
                    "id": str(record.id),
                    "content_hash": record.content_hash,
                    "source": record.source,
                    "source_type": record.source_type.value,
                    "title": record.title,
                    "publish_time": record.publish_time,
                    "fetch_time": record.fetch_time,
                    "process_time": record.process_time,
                    "available_time": record.available_time,
                    "status": record.status.value,
                    "payload": _dump(record),
                }
            )
        if not rows:
            return 0
        self.postgres.execute_many(
            f"""
            INSERT INTO {self.schema}.information
            (
                id, content_hash, source, source_type, title, publish_time,
                fetch_time, process_time, available_time, status, payload
            )
            VALUES
            (
                :id, :content_hash, :source, :source_type, :title, :publish_time,
                :fetch_time, :process_time, :available_time, :status,
                CAST(:payload AS JSONB)
            )
            ON CONFLICT(content_hash) DO UPDATE SET
                id = excluded.id,
                source = excluded.source,
                source_type = excluded.source_type,
                title = excluded.title,
                publish_time = excluded.publish_time,
                fetch_time = excluded.fetch_time,
                process_time = excluded.process_time,
                available_time = excluded.available_time,
                status = excluded.status,
                payload = excluded.payload
            """,
            rows,
        )
        return len(rows)

    def list_information(
        self,
        *,
        limit: int = 100,
        source_type: SourceType | str | None = None,
        status: InformationStatus | str | None = None,
        available_before: datetime | None = None,
    ) -> list[RawInformation]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": _limit(limit)}
        if source_type is not None:
            clauses.append("source_type = :source_type")
            params["source_type"] = SourceType(source_type).value
        if status is not None:
            clauses.append("status = :status")
            params["status"] = InformationStatus(status).value
        before = ensure_utc(available_before, field_name="available_before")
        if before is not None:
            clauses.append("available_time <= :available_before")
            params["available_before"] = before
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.postgres.query(
            f"""
            SELECT payload
            FROM {self.schema}.information
            {where}
            ORDER BY publish_time DESC, id
            LIMIT :limit
            """,
            params,
        )
        return [RawInformation.from_dict(_mapping(row["payload"])) for row in rows]

    def save_events(self, events: Sequence[MarketEvent]) -> int:
        rows = [
            {
                "id": str(event.id),
                "event_type": event.event_type.value,
                "title": event.title,
                "importance": event.importance,
                "heat_score": event.heat_score,
                "first_publish_time": event.first_publish_time,
                "available_time": event.last_update_time,
                "payload": _dump(event),
            }
            for event in events
        ]
        if not rows:
            return 0
        self.postgres.execute_many(
            f"""
            INSERT INTO {self.schema}.events
            (
                id, event_type, title, importance, heat_score,
                first_publish_time, available_time, payload
            )
            VALUES
            (
                :id, :event_type, :title, :importance, :heat_score,
                :first_publish_time, :available_time, CAST(:payload AS JSONB)
            )
            ON CONFLICT(id) DO UPDATE SET
                event_type = excluded.event_type,
                title = excluded.title,
                importance = excluded.importance,
                heat_score = excluded.heat_score,
                first_publish_time = excluded.first_publish_time,
                available_time = excluded.available_time,
                payload = excluded.payload
            """,
            rows,
        )
        return len(rows)

    def list_events(
        self,
        *,
        limit: int = 50,
        event_type: str | None = None,
        available_before: datetime | None = None,
    ) -> list[MarketEvent]:
        clauses: list[str] = []
        params: dict[str, Any] = {"limit": _limit(limit)}
        if event_type is not None:
            clauses.append("event_type = :event_type")
            params["event_type"] = str(event_type).upper()
        before = ensure_utc(available_before, field_name="available_before")
        if before is not None:
            clauses.append("available_time <= :available_before")
            params["available_before"] = before
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.postgres.query(
            f"""
            SELECT payload
            FROM {self.schema}.events
            {where}
            ORDER BY heat_score DESC, first_publish_time DESC, id
            LIMIT :limit
            """,
            params,
        )
        return [MarketEvent.from_dict(_mapping(row["payload"])) for row in rows]

    def get_event(self, event_id: str) -> MarketEvent | None:
        row = self.postgres.query_one(
            f"SELECT payload FROM {self.schema}.events WHERE id = :id",
            {"id": str(event_id)},
        )
        return MarketEvent.from_dict(_mapping(row["payload"])) if row else None

    def save_themes(self, themes: Sequence[Theme]) -> int:
        rows = [
            {
                "id": str(theme.id),
                "code": theme.code,
                "name": theme.name,
                "state": theme.state.value,
                "category": theme.category.value,
                "current_heat": theme.current_heat_score,
                "forward_heat": theme.forward_heat_score,
                "crowding": theme.crowding_score,
                "updated_at": theme.updated_at,
                "payload": _dump(theme),
            }
            for theme in themes
        ]
        if not rows:
            return 0
        self.postgres.execute_many(
            f"""
            INSERT INTO {self.schema}.themes
            (
                id, code, name, state, category, current_heat,
                forward_heat, crowding, updated_at, payload
            )
            VALUES
            (
                :id, :code, :name, :state, :category, :current_heat,
                :forward_heat, :crowding, :updated_at, CAST(:payload AS JSONB)
            )
            ON CONFLICT(id) DO UPDATE SET
                code = excluded.code,
                name = excluded.name,
                state = excluded.state,
                category = excluded.category,
                current_heat = excluded.current_heat,
                forward_heat = excluded.forward_heat,
                crowding = excluded.crowding,
                updated_at = excluded.updated_at,
                payload = excluded.payload
            """,
            rows,
        )
        return len(rows)

    def list_themes(
        self,
        *,
        limit: int = 100,
        state: str | None = None,
    ) -> list[Theme]:
        params: dict[str, Any] = {"limit": _limit(limit)}
        where = ""
        if state is not None:
            where = "WHERE state = :state"
            params["state"] = str(state).upper()
        rows = self.postgres.query(
            f"""
            SELECT payload
            FROM {self.schema}.themes
            {where}
            ORDER BY current_heat DESC, forward_heat DESC, name
            LIMIT :limit
            """,
            params,
        )
        return [Theme.from_dict(_mapping(row["payload"])) for row in rows]

    def get_theme(self, key: str) -> Theme | None:
        row = self.postgres.query_one(
            f"""
            SELECT payload
            FROM {self.schema}.themes
            WHERE id = :key OR code = :key OR name = :key
            LIMIT 1
            """,
            {"key": str(key)},
        )
        return Theme.from_dict(_mapping(row["payload"])) if row else None

    def save_brief(self, brief: InformationBrief) -> None:
        self.postgres.execute(
            f"""
            INSERT INTO {self.schema}.briefs
            (
                id, brief_type, generated_at, available_time, payload
            )
            VALUES
            (
                :id, :brief_type, :generated_at, :available_time,
                CAST(:payload AS JSONB)
            )
            ON CONFLICT(id) DO UPDATE SET
                brief_type = excluded.brief_type,
                generated_at = excluded.generated_at,
                available_time = excluded.available_time,
                payload = excluded.payload
            """,
            {
                "id": str(brief.id),
                "brief_type": brief.brief_type.value,
                "generated_at": brief.generated_at,
                "available_time": brief.available_time,
                "payload": _dump(brief),
            },
        )

    def get_morning_brief(self) -> InformationBrief | None:
        row = self.postgres.query_one(
            f"""
            SELECT payload
            FROM {self.schema}.briefs
            WHERE brief_type = :brief_type
            ORDER BY available_time DESC, generated_at DESC, id
            LIMIT 1
            """,
            {"brief_type": BriefType.MORNING.value},
        )
        return InformationBrief.from_dict(_mapping(row["payload"])) if row else None

    def save_notifications(self, notifications: Sequence[NotificationEvent]) -> int:
        rows = [
            {
                "id": str(notification.id),
                "dedup_key": notification.dedup_key,
                "severity": notification.severity.value,
                "created_at": notification.created_at,
                "payload": _dump(notification),
            }
            for notification in notifications
        ]
        if not rows:
            return 0
        self.postgres.execute_many(
            f"""
            INSERT INTO {self.schema}.notifications
            (
                id, dedup_key, severity, created_at, payload
            )
            VALUES
            (
                :id, :dedup_key, :severity, :created_at, CAST(:payload AS JSONB)
            )
            ON CONFLICT(dedup_key) DO UPDATE SET
                id = excluded.id,
                severity = excluded.severity,
                created_at = excluded.created_at,
                payload = excluded.payload
            """,
            rows,
        )
        return len(rows)

    def list_notifications(
        self,
        *,
        limit: int = 100,
        severity: str | None = None,
    ) -> list[NotificationEvent]:
        params: dict[str, Any] = {"limit": _limit(limit)}
        where = ""
        if severity is not None:
            where = "WHERE severity = :severity"
            params["severity"] = str(severity).upper()
        rows = self.postgres.query(
            f"""
            SELECT payload
            FROM {self.schema}.notifications
            {where}
            ORDER BY created_at DESC, id
            LIMIT :limit
            """,
            params,
        )
        return [
            NotificationEvent.from_dict(_mapping(row["payload"]))
            for row in rows
        ]

    def counts(self) -> dict[str, int]:
        names = ("information", "events", "themes", "briefs", "notifications")
        return {
            name: int(
                (
                    self.postgres.query_one(
                        f"SELECT COUNT(*) AS count FROM {self.schema}.{name}"
                    )
                    or {"count": 0}
                )["count"]
            )
            for name in names
        }


def _prepare_information(item: RawInformation) -> RawInformation:
    content_hash = item.content_hash or compute_content_hash(item.title, item.content)
    if item.content_hash == content_hash and str(item.id) == str(
        information_id(content_hash)
    ):
        return item
    return RawInformation(
        id=information_id(content_hash),
        source=item.source,
        source_type=item.source_type,
        url=item.url,
        title=item.title,
        content=item.content,
        author=item.author,
        event_time=item.event_time,
        publish_time=item.publish_time,
        fetch_time=item.fetch_time,
        process_time=item.process_time,
        available_time=item.available_time,
        language=item.language,
        raw_payload=item.raw_payload,
        content_hash=content_hash,
        status=item.status,
    )


def _dump(value: Any) -> str:
    return json.dumps(value.to_dict(), ensure_ascii=False, separators=(",", ":"))


def _load(value: str | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    payload = json.loads(str(value))
    if not isinstance(payload, Mapping):
        raise TypeError("persisted intelligence payload must be an object")
    return payload


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return _load(value)


def _limit(value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = 100
    return max(1, min(5000, parsed))


def _identifier(value: str) -> str:
    normalized = str(value).strip()
    if not normalized.replace("_", "").isalnum():
        raise ValueError("schema must be a simple SQL identifier")
    return normalized


def _postgres_schema(schema: str) -> tuple[str, ...]:
    return (
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.information (
            id TEXT PRIMARY KEY,
            content_hash TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            publish_time TIMESTAMPTZ NOT NULL,
            fetch_time TIMESTAMPTZ NOT NULL,
            process_time TIMESTAMPTZ,
            available_time TIMESTAMPTZ,
            status TEXT NOT NULL,
            payload JSONB NOT NULL
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_information_available
        ON {schema}.information (available_time DESC)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_information_source_type
        ON {schema}.information (source_type, publish_time DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            importance DOUBLE PRECISION NOT NULL,
            heat_score DOUBLE PRECISION NOT NULL,
            first_publish_time TIMESTAMPTZ NOT NULL,
            available_time TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_events_available
        ON {schema}.events (available_time DESC)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_events_type
        ON {schema}.events (event_type, heat_score DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.themes (
            id TEXT PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            state TEXT NOT NULL,
            category TEXT NOT NULL,
            current_heat DOUBLE PRECISION NOT NULL,
            forward_heat DOUBLE PRECISION NOT NULL,
            crowding DOUBLE PRECISION NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_themes_heat
        ON {schema}.themes (current_heat DESC, forward_heat DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.briefs (
            id TEXT PRIMARY KEY,
            brief_type TEXT NOT NULL,
            generated_at TIMESTAMPTZ NOT NULL,
            available_time TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_briefs_type
        ON {schema}.briefs (brief_type, generated_at DESC)
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.notifications (
            id TEXT PRIMARY KEY,
            dedup_key TEXT NOT NULL UNIQUE,
            severity TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            payload JSONB NOT NULL
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS idx_intelligence_notifications_created
        ON {schema}.notifications (created_at DESC)
        """,
    )


__all__ = [
    "IntelligenceRepository",
    "PostgresIntelligenceRepository",
    "SqliteIntelligenceRepository",
]
