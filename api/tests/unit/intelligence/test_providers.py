from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx

from xquant.intelligence.domain import SourceType
from xquant.intelligence.providers import RssInformationSource


def test_rss_source_parses_rss_and_atom_without_publisher_specific_logic() -> None:
    rss = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><item>
      <title>RSS item</title>
      <link>https://example.com/rss</link>
      <description><![CDATA[<p>RSS content</p>]]></description>
      <pubDate>Mon, 15 Sep 2026 01:00:00 GMT</pubDate>
      <guid>rss-1</guid>
    </item></channel></rss>"""
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Atom item</title>
        <link href="https://example.com/atom" />
        <summary>Atom content</summary>
        <updated>2026-09-15T02:00:00Z</updated>
        <id>atom-1</id>
      </entry>
    </feed>"""
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=rss if calls == 1 else atom, request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            source = RssInformationSource(
                "https://example.com/feed",
                source="example",
                source_type=SourceType.NEWS,
                client=client,
            )
            rss_items = await source.fetch(
                datetime(2026, 9, 15, tzinfo=UTC),
                datetime(2026, 9, 15, 3, tzinfo=UTC),
            )
            atom_items = await source.fetch(
                datetime(2026, 9, 15, tzinfo=UTC),
                datetime(2026, 9, 15, 3, tzinfo=UTC),
            )

        assert rss_items[0].title == "RSS item"
        assert rss_items[0].content == "<p>RSS content</p>"
        assert rss_items[0].url == "https://example.com/rss"
        assert atom_items[0].title == "Atom item"
        assert atom_items[0].content == "Atom content"
        assert atom_items[0].url == "https://example.com/atom"

    asyncio.run(run())
