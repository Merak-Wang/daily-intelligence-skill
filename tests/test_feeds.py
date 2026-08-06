import asyncio
from pathlib import Path

import httpx

from daily_intelligence.config import SourceConfig
from daily_intelligence.feeds import (
    discover_feed_urls,
    fetch_feed,
    looks_like_feed,
    parse_feed_document,
)


def _source() -> SourceConfig:
    return SourceConfig(
        id="example",
        name="Example News",
        url="https://news.example/",
        include_domains=["news.example"],
        module="information",
        category="international",
        tier=1,
        role="primary",
    )


def test_rss_parser_preserves_time_image_and_missing_time_fallback():
    rss = b"""<?xml version="1.0"?>
    <rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
      <channel>
        <item>
          <title>Major policy update changes the regional outlook</title>
          <link>https://news.example/policy-update</link>
          <pubDate>Fri, 24 Jul 2026 01:30:00 GMT</pubDate>
          <description><![CDATA[<p>A concrete public summary.</p>]]></description>
          <media:thumbnail
            url="https://static.example/assets/grey-placeholder.png"/>
          <media:content url="https://news.example/images/policy.jpg" type="image/jpeg"/>
        </item>
        <item>
          <title>Second detailed report without a publication timestamp</title>
          <link>https://news.example/second-report</link>
          <description>A second public summary.</description>
        </item>
      </channel>
    </rss>"""

    items = parse_feed_document(
        rss,
        _source(),
        "https://news.example/feed.xml",
        "2026-07-24T10:00:00+08:00",
        "Asia/Shanghai",
        max_items=10,
    )

    assert len(items) == 2
    assert items[0].published_at == "2026-07-24T09:30:00+08:00"
    assert items[0].image_url == "https://news.example/images/policy.jpg"
    assert items[0].description == "A concrete public summary."
    assert items[1].published_at is None
    assert items[1].metadata["publication_time_missing"] is True
    assert items[1].discovered_at == "2026-07-24T10:00:00+08:00"


def test_atom_parser_drops_future_dated_entry_and_keeps_provider():
    atom = b"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Valid research update with reproducible benchmark details</title>
        <link href="https://news.example/research"/>
        <published>2026-07-24T01:00:00Z</published>
        <summary>Benchmark details are available publicly.</summary>
        <source><title>Original Lab</title><link href="https://lab.example/"/></source>
      </entry>
      <entry>
        <title>Impossible future report should not enter the index</title>
        <link href="https://news.example/future"/>
        <published>2026-07-25T12:00:00Z</published>
      </entry>
    </feed>"""

    items = parse_feed_document(
        atom,
        _source(),
        "https://news.example/atom.xml",
        "2026-07-24T10:00:00+08:00",
        "Asia/Shanghai",
        max_items=10,
    )

    assert [item.title for item in items] == [
        "Valid research update with reproducible benchmark details"
    ]
    assert items[0].original_provider == "Original Lab"
    assert items[0].metadata["original_provider_url"] == "https://lab.example/"


def test_feed_parser_can_choose_publication_order_without_losing_top_rank():
    rss = b"""<rss><channel>
      <item>
        <title>Older article is the original first feed result</title>
        <link>https://news.example/original-first</link>
        <pubDate>Tue, 12 Sep 2023 01:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Newer article is the original second feed result</title>
        <link>https://news.example/newer-second</link>
        <pubDate>Fri, 24 Jul 2026 01:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    source = _source()
    source.item_order = "published_at"

    items = parse_feed_document(
        rss,
        source,
        "https://news.example/feed.xml",
        "2026-07-24T10:00:00+08:00",
        "Asia/Shanghai",
        max_items=1,
    )

    assert [item.url for item in items] == ["https://news.example/newer-second"]
    assert items[0].metadata["source_rank"] == 2


def test_feed_sniffing_and_html_discovery():
    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml" href="/rss.xml">
      <link rel="alternate" type="application/atom+xml" href="https://cdn.example/atom">
    </head></html>
    """

    assert looks_like_feed("<rss><channel/></rss>")
    assert not looks_like_feed("<!doctype html><html><body>captcha</body></html>")
    assert discover_feed_urls(html, "https://news.example/") == [
        "https://news.example/rss.xml",
        "https://cdn.example/atom",
    ]


def test_conditional_feed_cache_reuses_304_items(tmp_path: Path):
    requests: list[httpx.Request] = []
    rss = b"""<rss><channel><item>
      <title>A sufficiently detailed cached news headline</title>
      <link>https://news.example/cached</link>
      <pubDate>Fri, 24 Jul 2026 01:00:00 GMT</pubDate>
    </item></channel></rss>"""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200,
                content=rss,
                headers={"etag": '"feed-v1"', "content-type": "application/rss+xml"},
            )
        return httpx.Response(304)

    async def run() -> tuple:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            first = await fetch_feed(
                client,
                _source(),
                "https://news.example/rss.xml",
                tmp_path,
                "Asia/Shanghai",
                max_bytes=100_000,
                max_items=10,
                refresh_interval_minutes=30,
                force=True,
            )
            second = await fetch_feed(
                client,
                _source(),
                "https://news.example/rss.xml",
                tmp_path,
                "Asia/Shanghai",
                max_bytes=100_000,
                max_items=10,
                refresh_interval_minutes=30,
                force=True,
            )
            return first, second

    first, second = asyncio.run(run())

    assert len(first.items) == 1
    assert len(second.items) == 1
    assert second.cache_state == "not_modified"
    assert requests[1].headers["if-none-match"] == '"feed-v1"'
