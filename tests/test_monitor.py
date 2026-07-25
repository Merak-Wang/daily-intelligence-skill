import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from daily_intelligence.config import (
    AppConfig,
    BrowserConfig,
    MonitorConfig,
    SourceConfig,
)
from daily_intelligence.dashboard import create_monitor_server
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
    )
    assert len(cached["monitor_example"].items) == 1
    assert cached["monitor_example"].challenge["monitor_cache"] is True


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
