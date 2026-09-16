from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, runtime_checkable
from xml.etree import ElementTree

import httpx

from .domain import (
    InformationStatus,
    RawInformation,
    SourceType,
    compute_content_hash,
    ensure_utc,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class RateLimit:
    requests_per_second: float = 1.0
    burst: int = 1
    retry_after_seconds: float = 0.0


@runtime_checkable
class InformationSource(Protocol):
    async def fetch(
        self,
        start_time: datetime,
        end_time: datetime,
        cursor: str | None = None,
    ) -> list[RawInformation]:
        ...

    async def health_check(self) -> bool:
        ...

    async def get_rate_limit(self) -> RateLimit:
        ...


class InMemoryInformationSource:
    def __init__(self, items: Sequence[RawInformation]) -> None:
        self._items = [replace(item) for item in items]

    async def fetch(
        self,
        start_time: datetime,
        end_time: datetime,
        cursor: str | None = None,
    ) -> list[RawInformation]:
        start = ensure_utc(start_time, field_name="start_time")
        end = ensure_utc(end_time, field_name="end_time")
        if start is None or end is None or start > end:
            raise ValueError("start_time and end_time must define a valid UTC range")
        offset = int(cursor or 0)
        matched = [
            item
            for item in self._items
            if start <= item.publish_time <= end
        ]
        return [replace(item) for item in matched[offset:]]

    async def health_check(self) -> bool:
        return True

    async def get_rate_limit(self) -> RateLimit:
        return RateLimit(requests_per_second=float("inf"), burst=0)


class RssInformationSource:
    """Configurable RSS/Atom source that does not assume a specific publisher."""

    def __init__(
        self,
        feed_url: str,
        *,
        source: str,
        source_type: SourceType | str = SourceType.NEWS,
        language: str = "zh-CN",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        headers: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        requests_per_second: float = 1.0,
    ) -> None:
        if not str(feed_url).strip():
            raise ValueError("feed_url cannot be empty")
        if not str(source).strip():
            raise ValueError("source cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self.feed_url = str(feed_url).strip()
        self.source = str(source).strip()
        self.source_type = SourceType(source_type)
        self.language = str(language).strip()
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))
        self.headers = dict(headers or {})
        self._client = client
        self._rate_limit = RateLimit(requests_per_second=requests_per_second)

    async def fetch(
        self,
        start_time: datetime,
        end_time: datetime,
        cursor: str | None = None,
    ) -> list[RawInformation]:
        start = ensure_utc(start_time, field_name="start_time")
        end = ensure_utc(end_time, field_name="end_time")
        if start is None or end is None or start > end:
            raise ValueError("start_time and end_time must define a valid UTC range")
        response = await self._get()
        items = self._parse_feed(response.content)
        matched = [
            item
            for item in items
            if start <= item.publish_time <= end
        ]
        offset = int(cursor or 0)
        return matched[offset:]

    async def health_check(self) -> bool:
        try:
            await self._get()
        except (httpx.HTTPError, ElementTree.ParseError, ValueError):
            return False
        return True

    async def get_rate_limit(self) -> RateLimit:
        return self._rate_limit

    async def _get(self) -> httpx.Response:
        if self._client is not None:
            return await self._request(self._client)
        timeout = httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, headers=self.headers) as client:
            return await self._request(client)

    async def _request(self, client: httpx.AsyncClient) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await client.get(self.feed_url)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                delay = self.retry_backoff_seconds * (2**attempt)
                if delay:
                    await asyncio.sleep(delay)
        assert last_error is not None
        raise last_error

    def _parse_feed(self, payload: bytes) -> list[RawInformation]:
        root = ElementTree.fromstring(payload)
        entries = _descendants_by_local_name(root, "item")
        if not entries:
            entries = _descendants_by_local_name(root, "entry")
        fetched_at = utc_now()
        items: list[RawInformation] = []
        for entry in entries:
            title = _child_text(entry, "title")
            content = (
                _child_text(entry, "encoded")
                or _child_text(entry, "content")
                or _child_text(entry, "description")
                or _child_text(entry, "summary")
            )
            url = _entry_url(entry)
            author = _child_text(entry, "author") or _child_text(entry, "creator")
            publish_time = _entry_datetime(entry) or fetched_at
            publish_time = min(publish_time, fetched_at)
            raw_payload = {
                "feed_url": self.feed_url,
                "entry_id": _child_text(entry, "guid") or _child_text(entry, "id"),
                "categories": _child_texts(entry, "category"),
                "cursor": _child_text(entry, "id") or url,
            }
            items.append(
                RawInformation(
                    source=self.source,
                    source_type=self.source_type,
                    url=url,
                    title=title,
                    content=content,
                    author=author or None,
                    publish_time=publish_time,
                    fetch_time=fetched_at,
                    language=self.language,
                    raw_payload=raw_payload,
                    content_hash=compute_content_hash(title, content),
                    status=InformationStatus.COLLECTED,
                )
            )
        return items


class _TypedInformationSource:
    default_source_type = SourceType.NEWS

    def __init__(
        self,
        source: InformationSource | None = None,
        *,
        feed_url: str | None = None,
        name: str | None = None,
        language: str = "zh-CN",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if source is None:
            if not feed_url:
                raise ValueError("source or feed_url is required")
            source = RssInformationSource(
                feed_url,
                source=name or self.default_source_type.value,
                source_type=self.default_source_type,
                language=language,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                client=client,
            )
        self.source = source

    async def fetch(
        self,
        start_time: datetime,
        end_time: datetime,
        cursor: str | None = None,
    ) -> list[RawInformation]:
        items = await self.source.fetch(start_time, end_time, cursor)
        return [
            replace(item, source_type=self.default_source_type)
            for item in items
        ]

    async def health_check(self) -> bool:
        return await self.source.health_check()

    async def get_rate_limit(self) -> RateLimit:
        return await self.source.get_rate_limit()


class NewsProvider(_TypedInformationSource):
    default_source_type = SourceType.NEWS


class PolicyProvider(_TypedInformationSource):
    default_source_type = SourceType.POLICY


class AnnouncementProvider(_TypedInformationSource):
    default_source_type = SourceType.ANNOUNCEMENT


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _children_by_local_name(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(node) if _local_name(child.tag) == name.lower()]


def _descendants_by_local_name(
    node: ElementTree.Element,
    name: str,
) -> list[ElementTree.Element]:
    return [child for child in node.iter() if _local_name(child.tag) == name.lower()]


def _child_text(node: ElementTree.Element, name: str) -> str:
    for child in list(node):
        if _local_name(child.tag) != name.lower():
            continue
        if name.lower() == "author":
            nested_name = _child_text(child, "name")
            if nested_name:
                return nested_name
        return " ".join(part.strip() for part in child.itertext() if part.strip())
    return ""


def _child_texts(node: ElementTree.Element, name: str) -> list[str]:
    return [
        " ".join(part.strip() for part in child.itertext() if part.strip())
        for child in list(node)
        if _local_name(child.tag) == name.lower()
    ]


def _entry_url(entry: ElementTree.Element) -> str | None:
    for child in list(entry):
        if _local_name(child.tag) != "link":
            continue
        value = str(child.attrib.get("href") or "").strip()
        if not value:
            value = " ".join(part.strip() for part in child.itertext() if part.strip())
        if value:
            return value
    return None


def _entry_datetime(entry: ElementTree.Element) -> datetime | None:
    for name in ("pubdate", "published", "updated", "date"):
        value = _child_text(entry, name)
        if not value:
            continue
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return ensure_utc(text, field_name="publish_time")
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = [
    "AnnouncementProvider",
    "InMemoryInformationSource",
    "InformationSource",
    "NewsProvider",
    "PolicyProvider",
    "RateLimit",
    "RssInformationSource",
]
