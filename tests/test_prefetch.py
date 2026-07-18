from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path

import httpx

from daily_intelligence.collector import collect_source
from daily_intelligence.config import AppConfig, BrowserConfig, SourceConfig
from daily_intelligence.models import ArticleItem, SourceResult, SourceStatus
from daily_intelligence.prefetch import _prefetch_all, html_index_rows, page_needs_browser


def _source(
    source_id: str = "example",
    url: str = "https://example.com/news",
    explore_urls: list[str] | None = None,
) -> SourceConfig:
    domain = url.split("/")[2]
    return SourceConfig(
        id=source_id,
        name=source_id,
        url=url,
        include_domains=[domain],
        article_patterns=[rf"^https://{domain}/articles/"],
        explore_urls=explore_urls or [],
        report_target=1,
        report_max=2,
    )


def test_public_html_rows_preserve_title_link_and_time_context():
    title, rows = html_index_rows(
        """
        <html><head><title>Example News</title></head><body>
          <article><time datetime="2026-07-17T05:00:00+08:00"></time>
            <a href="/articles/one"><h2>A sufficiently long public headline</h2></a>
          </article>
        </body></html>
        """
    )

    assert title == "Example News"
    assert rows[0]["href"] == "/articles/one"
    assert rows[0]["title"] == "A sufficiently long public headline"
    assert "2026-07-17" in rows[0]["context"]


def test_parallel_prefetch_obeys_global_and_per_domain_limits(tmp_path: Path):
    config = AppConfig(
        timezone="Asia/Shanghai",
        browser=BrowserConfig(
            collection_global_concurrency=2,
            collection_per_domain_concurrency=1,
        ),
        sources=[],
    )
    sources = [
        _source(
            "alpha",
            "https://alpha.example/news",
            ["https://alpha.example/world"],
        ),
        _source("beta", "https://beta.example/news"),
    ]
    active = 0
    maximum_active = 0
    active_by_domain: defaultdict[str, int] = defaultdict(int)
    maximum_by_domain: defaultdict[str, int] = defaultdict(int)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        domain = request.url.host
        active += 1
        active_by_domain[domain] += 1
        maximum_active = max(maximum_active, active)
        maximum_by_domain[domain] = max(
            maximum_by_domain[domain], active_by_domain[domain]
        )
        await asyncio.sleep(0.02)
        active_by_domain[domain] -= 1
        active -= 1
        return httpx.Response(
            200,
            text=(
                '<article><a href="/articles/story">'
                "A sufficiently long public headline</a></article>"
            ),
        )

    results = asyncio.run(
        _prefetch_all(
            sources,
            config,
            tmp_path,
            transport=httpx.MockTransport(handler),
        )
    )

    assert len(results) == 3
    assert maximum_active == 2
    assert maximum_by_domain["alpha.example"] == 1
    assert all(result.status == SourceStatus.SUCCESS for result in results.values())


def test_successful_prefetch_bypasses_edge(tmp_path: Path):
    source = _source()
    config = AppConfig(
        timezone="Asia/Shanghai",
        browser=BrowserConfig(),
        sources=[source],
    )
    item = ArticleItem(
        item_id="example-1",
        source_id=source.id,
        source_name=source.name,
        title="A sufficiently long public headline",
        url="https://example.com/articles/one",
        canonical_url="https://example.com/articles/one",
        discovered_at="2026-07-17T06:00:00+08:00",
    )
    prefetched = SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=SourceStatus.SUCCESS,
        collected_at="2026-07-17T06:00:00+08:00",
        items=[item],
    )

    result = collect_source(
        None,
        source,
        config,
        tmp_path,
        {(source.id, source.url): prefetched},
    )

    assert result.status == SourceStatus.SUCCESS
    assert result.items == [item]


def test_rate_limit_is_not_retried_in_edge_but_empty_public_html_is():
    rate_limited = SourceResult(
        source_id="reuters",
        source_name="Reuters",
        source_url="https://reuters.com",
        status=SourceStatus.RATE_LIMITED,
        collected_at="2026-07-17T06:00:00+08:00",
    )
    empty = SourceResult(
        source_id="example",
        source_name="Example",
        source_url="https://example.com",
        status=SourceStatus.NO_ITEMS,
        collected_at="2026-07-17T06:00:00+08:00",
    )

    assert page_needs_browser(rate_limited) is False
    assert page_needs_browser(empty) is True
