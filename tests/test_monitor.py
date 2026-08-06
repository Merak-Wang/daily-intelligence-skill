import threading
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from daily_intelligence.collector import collect_source
from daily_intelligence.config import (
    AppConfig,
    BrowserConfig,
    MonitorConfig,
    SourceConfig,
)
from daily_intelligence.dashboard import create_monitor_server
from daily_intelligence.models import ArticleItem, SourceResult, SourceStatus
from daily_intelligence.monitor import (
    fresh_monitor_snapshot_path,
    load_monitor_results,
    refresh_monitor,
)
from daily_intelligence.utils import read_json, write_json


def _config() -> AppConfig:
    source = SourceConfig(
        id="monitor_example",
        name="Monitor Example",
        url="https://news.example/",
        feed_urls=["https://news.example/rss.xml"],
        module="information",
        category="international",
        role="primary",
        tier=1,
        report_target=1,
        report_max=1,
    )
    return AppConfig(
        timezone="Asia/Shanghai",
        browser=BrowserConfig(),
        sources=[source],
        monitor=MonitorConfig(
            sources_file=None,
            html_fallback=False,
            auto_discover_feeds=False,
            max_age_hours=168,
        ),
    )


def test_monitor_snapshot_is_zero_token_and_reusable_by_editions(tmp_path: Path):
    now = datetime(2026, 7, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    rss = b"""<rss><channel><item>
      <title>Major regional agreement receives a detailed official update</title>
      <link>https://news.example/agreement</link>
      <pubDate>Fri, 24 Jul 2026 01:00:00 GMT</pubDate>
      <description>A public summary for the monitor card.</description>
    </item></channel></rss>"""

    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=rss,
            headers={"content-type": "application/rss+xml"},
        )
    )
    config = _config()
    output = refresh_monitor(
        config,
        tmp_path,
        include_discovery=False,
        force=True,
        transport=transport,
        now=now,
    )
    snapshot = read_json(output)

    assert snapshot["token_usage"] == 0
    assert snapshot["summary"]["item_count"] == 1
    assert snapshot["items"][0]["published_at"]
    assert snapshot["clusters"][0]["item_ids"] == [
        snapshot["items"][0]["item_id"]
    ]

    cached = load_monitor_results(
        tmp_path,
        config.sources,
        config.timezone,
        max_age_minutes=90,
        now=now,
    )
    assert len(cached["monitor_example"].items) == 1
    assert cached["monitor_example"].challenge["monitor_cache"] is True


def test_monitor_cache_honors_source_or_publication_order(tmp_path: Path):
    now = datetime(2026, 7, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    rss = b"""<rss><channel>
      <item>
        <title>Older story remains the source's original top result</title>
        <link>https://news.example/original-top</link>
        <pubDate>Mon, 12 Sep 2023 01:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Newer story appears second in the source feed</title>
        <link>https://news.example/newer-second</link>
        <pubDate>Fri, 24 Jul 2026 02:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=rss,
            headers={"content-type": "application/rss+xml"},
        )
    )
    config = _config()
    refresh_monitor(
        config,
        tmp_path,
        include_discovery=False,
        force=True,
        transport=transport,
        now=now,
    )

    source_order = load_monitor_results(
        tmp_path,
        config.sources,
        config.timezone,
        max_age_minutes=90,
        now=now,
    )["monitor_example"].items
    published_config = _config()
    published_config.sources[0] = replace(
        published_config.sources[0], item_order="published_at"
    )
    published_order = load_monitor_results(
        tmp_path,
        published_config.sources,
        published_config.timezone,
        max_age_minutes=90,
        now=now,
    )["monitor_example"].items

    assert [item.url for item in source_order] == [
        "https://news.example/original-top",
        "https://news.example/newer-second",
    ]
    assert [item.metadata["source_rank"] for item in source_order] == [1, 2]
    assert [item.url for item in published_order] == [
        "https://news.example/newer-second",
        "https://news.example/original-top",
    ]
    assert [item.metadata["source_rank"] for item in published_order] == [2, 1]


def test_retained_monitor_history_cannot_satisfy_live_report_target(tmp_path: Path):
    now = datetime(2026, 7, 24, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    generated_at = now.isoformat()
    config = _config()
    source = replace(
        config.sources[0],
        report_target=15,
        report_max=15,
        max_items=20,
    )
    config.sources[0] = source

    def article(position: int, *, retained: bool = False) -> ArticleItem:
        prefix = "retained" if retained else "current"
        url = f"https://news.example/{prefix}/{position}"
        return ArticleItem(
            item_id=f"{prefix}-{position}",
            source_id=source.id,
            source_name=source.name,
            title=f"{prefix.title()} story {position}",
            url=url,
            canonical_url=url,
            discovered_at=generated_at,
            module=source.module,
            category=source.category,
            metadata={
                "source_rank": position,
                **(
                    {"retained_from_previous_snapshot": True}
                    if retained
                    else {}
                ),
            },
        )

    current = [article(position) for position in range(1, 11)]
    retained = [article(position, retained=True) for position in range(11, 21)]
    write_json(
        tmp_path / "monitor" / "snapshot.json",
        {
            "schema_version": "2.0",
            "generated_at": generated_at,
            "sources": [
                {
                    "source_id": source.id,
                    "source_name": source.name,
                    "source_url": source.url,
                    "status": "success",
                    "items_count": len(current),
                }
            ],
            "items": [item.to_dict() for item in [*current, *retained]],
        },
    )

    cached = load_monitor_results(
        tmp_path,
        [source],
        config.timezone,
        max_age_minutes=90,
        now=now,
    )[source.id]
    assert [item.item_id for item in cached.items] == [
        f"current-{position}" for position in range(1, 11)
    ]

    live_items = [article(position) for position in range(1, 21)]
    live = SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=SourceStatus.SUCCESS,
        collected_at=generated_at,
        final_url=source.url,
        items=live_items,
    )
    result = collect_source(
        None,
        source,
        config,
        tmp_path,
        prefetched_pages={(source.id, source.url): live},
        monitor_result=cached,
    )

    assert [item.item_id for item in result.items[:15]] == [
        f"current-{position}" for position in range(1, 16)
    ]
    assert not any(
        item.metadata.get("retained_from_previous_snapshot")
        for item in result.items
    )


def test_live_results_precede_an_insufficient_monitor_snapshot(tmp_path: Path):
    generated_at = "2026-07-24T10:00:00+08:00"
    config = _config()
    source = replace(
        config.sources[0],
        report_target=4,
        report_max=4,
        max_items=4,
    )

    def article(slug: str) -> ArticleItem:
        url = f"https://news.example/{slug}"
        return ArticleItem(
            item_id=slug,
            source_id=source.id,
            source_name=source.name,
            title=f"Story {slug}",
            url=url,
            canonical_url=url,
            discovered_at=generated_at,
            module=source.module,
            category=source.category,
        )

    cached = SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=SourceStatus.SUCCESS,
        collected_at=generated_at,
        items=[article("a"), article("b")],
    )
    live = replace(
        cached,
        items=[article("x"), article("y"), article("a"), article("b")],
    )

    result = collect_source(
        None,
        source,
        config,
        tmp_path,
        prefetched_pages={(source.id, source.url): live},
        monitor_result=cached,
    )

    assert [item.item_id for item in result.items] == ["x", "y", "a", "b"]
    assert [item.metadata["source_rank"] for item in result.items] == [1, 2, 3, 4]


def test_monitor_snapshot_remains_a_fallback_when_live_collection_fails(
    tmp_path: Path,
    monkeypatch,
):
    generated_at = "2026-07-24T10:00:00+08:00"
    config = _config()
    source = replace(
        config.sources[0],
        report_target=3,
        report_max=3,
        max_items=3,
    )

    def article(slug: str) -> ArticleItem:
        url = f"https://news.example/{slug}"
        return ArticleItem(
            item_id=slug,
            source_id=source.id,
            source_name=source.name,
            title=f"Story {slug}",
            url=url,
            canonical_url=url,
            discovered_at=generated_at,
            module=source.module,
            category=source.category,
        )

    cached = SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=SourceStatus.SUCCESS,
        collected_at=generated_at,
        items=[article("a"), article("b")],
    )
    failed = replace(
        cached,
        status=SourceStatus.VERIFICATION_REQUIRED,
        items=[],
        error="HTTP 403",
    )
    monkeypatch.setattr(
        "daily_intelligence.collector.page_needs_browser",
        lambda _result: False,
    )

    result = collect_source(
        None,
        source,
        config,
        tmp_path,
        prefetched_pages={(source.id, source.url): failed},
        monitor_result=cached,
    )

    assert [item.item_id for item in result.items] == ["a", "b"]
    assert result.status == SourceStatus.PARTIAL
    assert result.error == "HTTP 403"


def test_fresh_monitor_snapshot_avoids_an_unnecessary_network_refresh(
    tmp_path: Path,
):
    generated_at = "2026-07-24T10:00:00+08:00"
    snapshot_path = write_json(
        tmp_path / "monitor" / "snapshot.json",
        {
            "schema_version": "2.0",
            "generated_at": generated_at,
            "token_usage": 0,
            "sources": [],
            "items": [],
        },
    )

    fresh = fresh_monitor_snapshot_path(
        tmp_path,
        "Asia/Shanghai",
        90,
        now=datetime(2026, 7, 24, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    stale = fresh_monitor_snapshot_path(
        tmp_path,
        "Asia/Shanghai",
        20,
        now=datetime(2026, 7, 24, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert fresh == snapshot_path
    assert stale is None


def test_rss_only_refresh_marks_html_only_source_as_unsupported(tmp_path: Path):
    source = SourceConfig(
        id="html_only",
        name="HTML Only",
        url="https://news.example/",
        module="information",
        category="international",
        report_target=0,
        report_max=0,
    )
    config = AppConfig(
        timezone="Asia/Shanghai",
        browser=BrowserConfig(),
        sources=[source],
        monitor=MonitorConfig(
            sources_file=None,
            html_fallback=False,
            auto_discover_feeds=True,
        ),
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text="<html><head><title>News</title></head><body></body></html>",
            headers={"content-type": "text/html"},
        )
    )

    output = refresh_monitor(
        config,
        tmp_path,
        include_discovery=False,
        force=True,
        html_fallback=False,
        transport=transport,
    )
    snapshot = read_json(output)

    assert snapshot["sources"][0]["status"] == "unsupported"
    assert snapshot["sources"][0]["error"] == (
        "No RSS or Atom feed was declared or discovered"
    )
    assert "no_items" not in snapshot["summary"]["status_breakdown"]


def test_dashboard_serves_snapshot_read_only(tmp_path: Path):
    write_json(
        tmp_path / "monitor" / "snapshot.json",
        {
            "schema_version": "2.0",
            "generated_at": "2026-07-24T10:00:00+08:00",
            "token_usage": 0,
            "summary": {},
            "sources": [],
            "items": [],
            "clusters": [],
            "health": [],
            "pending_verifications": [],
        },
    )
    server = create_monitor_server(tmp_path, port=0, quiet=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        response = httpx.get(f"http://{host}:{port}/api/snapshot", timeout=5)
        page = httpx.get(f"http://{host}:{port}/", timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response.status_code == 200
    assert response.json()["token_usage"] == 0
    assert page.status_code == 200
    assert "日报情报台" in page.text
    assert "Content-Security-Policy" in page.headers
