from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from daily_intelligence.config import load_config
from daily_intelligence.content import _extract_pipeline, _run_http_extraction
from daily_intelligence.models import ContentStatus


def _item(item_id: str = "bbc-static") -> dict:
    return {
        "item_id": item_id,
        "source_id": "bbc_world",
        "source_name": "BBC",
        "title": "Original public headline",
        "url": f"https://www.bbc.com/news/articles/{item_id}",
        "content_status": ContentStatus.NOT_FETCHED,
        "metadata": {},
    }


def test_http_first_content_extraction_avoids_browser_for_static_article(
    tmp_path: Path,
):
    config = load_config()
    item = _item()
    article_text = " ".join(["verified public article detail"] * 180)
    document = (
        "<html><head>"
        '<meta property="og:title" content="Updated public headline">'
        '<meta property="article:published_time" content="2026-07-24T08:00:00Z">'
        '<meta property="og:image" content="/image/story.png">'
        "</head><body><article>"
        f"{article_text}"
        "</article></body></html>"
    )
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            content=document.encode(),
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
    )

    browser_targets = asyncio.run(
        _run_http_extraction(
            [item],
            config,
            tmp_path,
            transport=transport,
        )
    )

    assert browser_targets == []
    assert item["content_status"] == ContentStatus.FULL_TEXT
    assert item["metadata"]["content_acquisition"] == "http"
    assert item["published_at"] == "2026-07-24T08:00:00Z"
    assert item["image_url"] == "https://www.bbc.com/image/story.png"
    assert Path(item["content_path"]).is_file()


def test_http_first_content_extraction_uses_browser_only_for_javascript_shell(
    tmp_path: Path,
):
    config = load_config()
    item = _item("bbc-js-shell")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            text="<html><body><main id='app'>Loading</main></body></html>",
            headers={"Content-Type": "text/html"},
        )
    )

    browser_targets = asyncio.run(
        _run_http_extraction(
            [item],
            config,
            tmp_path,
            transport=transport,
        )
    )

    assert browser_targets == [item]
    assert item["content_status"] == ContentStatus.METADATA_ONLY


def test_http_access_failure_stays_verification_required_without_browser_retry(
    tmp_path: Path,
):
    config = load_config()
    item = _item("bbc-forbidden")
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            403,
            text="<html><title>Access denied</title></html>",
            headers={"Content-Type": "text/html"},
        )
    )

    browser_targets = asyncio.run(
        _run_http_extraction(
            [item],
            config,
            tmp_path,
            transport=transport,
        )
    )

    assert browser_targets == []
    assert item["content_status"] == ContentStatus.VERIFICATION_REQUIRED
    assert item["metadata"]["content_challenge"]["required"] is True


def test_content_pipeline_reuses_existing_successful_content(monkeypatch, tmp_path: Path):
    config = load_config()
    content_path = tmp_path / "content" / "bbc_world" / "cached" / "body.md"
    content_path.parent.mkdir(parents=True)
    content_path.write_text("cached article", encoding="utf-8")
    item = _item("cached")
    item.update(
        {
            "content_status": ContentStatus.FULL_TEXT,
            "content_path": str(content_path),
        }
    )

    async def unexpected_http(*_args, **_kwargs):
        raise AssertionError("HTTP must not run for reusable content")

    async def unexpected_browser(*_args, **_kwargs):
        raise AssertionError("browser must not run for reusable content")

    monkeypatch.setattr(
        "daily_intelligence.content._run_http_extraction",
        unexpected_http,
    )
    monkeypatch.setattr(
        "daily_intelligence.content._extract_with_browser",
        unexpected_browser,
    )

    metrics = asyncio.run(
        _extract_pipeline(
            [item],
            config,
            tmp_path,
            False,
            tmp_path / "profile",
            None,
        )
    )

    assert metrics["cache_hits"] == 1
    assert metrics["http_attempted"] == 0
    assert metrics["browser_fallback"] == 0
    assert item["metadata"]["content_acquisition"] == "cache"
