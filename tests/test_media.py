from __future__ import annotations

import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import jsonschema
import pytest
from PIL import Image

from daily_intelligence import media as media_module
from daily_intelligence.config import MediaConfig
from daily_intelligence.local_output import render_report_html
from daily_intelligence.media import (
    DownloadedImage,
    ImageDownloadError,
    assert_public_image_url,
    download_image,
    materialize_report_images,
)
from daily_intelligence.notion import (
    NotionPublisher,
    _prepare_image_uploads,
    backfill_report_images,
    report_to_blocks,
)
from daily_intelligence.reports import render_report_markdown
from daily_intelligence.utils import read_json, write_json


def _png_bytes() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (3, 2))
    image.putdata(
        [
            (35, 74, 112),
            (210, 106, 48),
            (240, 230, 175),
            (22, 120, 92),
            (150, 65, 135),
            (245, 245, 245),
        ]
    )
    image.save(output, format="PNG")
    return output.getvalue()


def _image_record(content: bytes, source_url: str = "https://cdn.example/story.png") -> dict:
    digest = hashlib.sha256(content).hexdigest()
    return {
        "source_url": source_url,
        "resolved_url": source_url,
        "local_path": f"media/images/{digest[:2]}/{digest}.png",
        "content_type": "image/png",
        "sha256": digest,
        "byte_size": len(content),
        "width": 3,
        "height": 2,
        "caption": "公开新闻配图",
        "credit": "Example News",
    }


def _report(image: dict | None = None) -> dict[str, Any]:
    brief = {
        "item_id": "example-1",
        "title": "A sufficiently detailed public news headline",
        "title_zh": "一条信息充分的公开新闻标题",
        "tldr": "这是一条用于验证图文流排版和图片上传的公开新闻摘要。",
        "importance": 80,
        "status": "NEW",
        "source_rank": 1,
        "source_rank_label": "来源Top1",
        "primary_source": {
            "id": "example",
            "name": "Example News",
            "url": "https://news.example/",
        },
        "source_ref": {
            "item_id": "example-1",
            "title": "A sufficiently detailed public news headline",
            "url": "https://news.example/story",
            "access": "metadata_only",
            "role": "evidence",
            "published_at": "2026-07-23T08:00:00+08:00",
        },
    }
    if image:
        brief["image"] = image
    return {
        "schema_version": "1.5",
        "report_id": "daily-2026-07-23-morning-r1",
        "date": "2026-07-23",
        "edition": "morning",
        "revision": 1,
        "generated_at": "2026-07-23T09:00:00+08:00",
        "title": "每日情报晨报 — 2026-07-23",
        "executive_summary": ["本版用于验证图文流。"],
        "sections": [
            {
                "id": "information.international",
                "module": "information",
                "category": "international",
                "title": "国际",
                "items": [],
                "briefs": [brief],
            }
        ],
        "analyses": [],
        "changes": [],
        "tomorrow_watch_items": [],
        "pending_verifications": [],
    }


def test_public_image_url_rejects_credentials_and_private_networks():
    with pytest.raises(ImageDownloadError, match="credentials"):
        assert_public_image_url("https://user:secret@example.com/image.jpg")
    with pytest.raises(ImageDownloadError, match="non-public"):
        assert_public_image_url("http://127.0.0.1/image.jpg")
    with pytest.raises(ImageDownloadError, match="port 80 or 443"):
        assert_public_image_url("https://example.com:8443/image.jpg")
    with pytest.raises(ImageDownloadError, match="known placeholder"):
        assert_public_image_url(
            "https://static.example/assets/grey-placeholder.png"
        )


def test_public_image_url_confirms_proxy_fake_ip_with_public_dns(monkeypatch):
    monkeypatch.setattr(
        media_module,
        "_resolve_system_addresses",
        lambda _hostname, _port: ("198.18.0.42",),
    )
    monkeypatch.setattr(
        media_module,
        "_resolve_public_dns_addresses",
        lambda _hostname: ("8.8.8.8",),
    )

    assert_public_image_url("https://cdn.example/story.jpg")


def test_public_image_url_rejects_unconfirmed_proxy_fake_ip(monkeypatch):
    monkeypatch.setattr(
        media_module,
        "_resolve_system_addresses",
        lambda _hostname, _port: ("198.18.0.42",),
    )
    monkeypatch.setattr(
        media_module,
        "_resolve_public_dns_addresses",
        lambda _hostname: ("10.0.0.8",),
    )

    with pytest.raises(ImageDownloadError, match="could not be confirmed"):
        assert_public_image_url("https://cdn.example/story.jpg")
    with pytest.raises(ImageDownloadError, match="non-public"):
        assert_public_image_url("https://198.18.0.42/story.jpg")


def test_download_image_follows_validated_redirect_and_reuses_content_addressed_file(
    tmp_path: Path,
):
    content = _png_bytes()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "news.example":
            return httpx.Response(302, headers={"Location": "https://cdn.example/story.png"})
        return httpx.Response(
            200,
            content=content,
            headers={"Content-Type": "image/png", "Content-Length": str(len(content))},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    validated: list[str] = []
    try:
        first = download_image(
            "https://news.example/image",
            tmp_path,
            MediaConfig(),
            client=client,
            url_validator=validated.append,
        )
        second = download_image(
            "https://news.example/image",
            tmp_path,
            MediaConfig(),
            client=client,
            url_validator=lambda _url: None,
        )
    finally:
        client.close()

    assert validated == [
        "https://news.example/image",
        "https://cdn.example/story.png",
    ]
    assert first.resolved_url == "https://cdn.example/story.png"
    assert first.content_type == "image/png"
    assert (tmp_path / first.local_path).read_bytes() == content
    assert first.reused is False
    assert second.reused is True
    assert len(requests) == 4


def test_download_image_rejects_html_and_does_not_persist_it(tmp_path: Path):
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b"<html>not an image</html>",
                headers={"Content-Type": "text/html"},
            )
        )
    )
    try:
        with pytest.raises(ImageDownloadError, match="valid raster image"):
            download_image(
                "https://news.example/image",
                tmp_path,
                MediaConfig(),
                client=client,
                url_validator=lambda _url: None,
            )
    finally:
        client.close()
    assert not (tmp_path / "media").exists()


def test_download_image_rejects_low_information_raster(tmp_path: Path):
    output = BytesIO()
    Image.new("RGB", (160, 90), "#eeeeee").save(output, format="PNG")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=output.getvalue(),
                headers={"Content-Type": "image/png"},
            )
        )
    )
    try:
        with pytest.raises(ImageDownloadError, match="low-information placeholder"):
            download_image(
                "https://news.example/opaque-asset.png",
                tmp_path,
                MediaConfig(),
                client=client,
                url_validator=lambda _url: None,
            )
    finally:
        client.close()
    assert not (tmp_path / "media").exists()


def test_download_image_serializes_unicode_paths_as_schema_safe_uris(
    tmp_path: Path,
):
    content = _png_bytes()
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=content,
                headers={"Content-Type": "image/png"},
            )
        )
    )
    try:
        downloaded = download_image(
            "https://news.example/2026/3.M3直線加速-scaled.jpg",
            tmp_path,
            MediaConfig(),
            client=client,
            url_validator=lambda _url: None,
        )
    finally:
        client.close()

    expected = (
        "https://news.example/2026/3.M3"
        "%E7%9B%B4%E7%B7%9A%E5%8A%A0%E9%80%9F-scaled.jpg"
    )
    assert downloaded.source_url == expected
    assert downloaded.resolved_url == expected


def test_materialize_report_images_prioritizes_importance_and_records_budget(
    tmp_path: Path,
):
    report = _report()
    low = dict(report["sections"][0]["briefs"][0])
    low.update({"item_id": "example-low", "importance": 20, "source_rank": 2})
    report["sections"][0]["briefs"].append(low)
    index = {
        "items": [
            {
                "item_id": "example-1",
                "title": "High importance",
                "url": "https://news.example/high",
                "source_name": "Example News",
                "image_url": "https://cdn.example/high.png",
            },
            {
                "item_id": "example-low",
                "title": "Low importance",
                "url": "https://news.example/low",
                "source_name": "Example News",
                "image_url": "https://cdn.example/low.png",
            },
        ]
    }
    content = _png_bytes()
    stored = _image_record(content)

    def fake_downloader(source_url, _data_dir, _config, **_kwargs):
        return DownloadedImage(
            source_url=source_url,
            resolved_url=source_url,
            local_path=stored["local_path"],
            content_type="image/png",
            sha256=stored["sha256"],
            byte_size=len(content),
            width=3,
            height=2,
            reused=False,
        )

    warnings = materialize_report_images(
        report,
        index,
        tmp_path,
        MediaConfig(max_images_per_report=1),
        downloader=fake_downloader,
    )

    high, low = report["sections"][0]["briefs"]
    assert warnings == []
    assert high["image"]["source_url"] == "https://cdn.example/high.png"
    assert "image" not in low
    assert report["media_metrics"] == {
        "candidates": 2,
        "attached": 1,
        "unique_files": 1,
        "reused_files": 0,
        "failed": 0,
        "skipped_budget": 1,
        "total_bytes": len(content),
    }


def test_materialize_report_images_tries_later_candidates_after_a_failure(
    tmp_path: Path,
):
    report = _report()
    fallback = dict(report["sections"][0]["briefs"][0])
    fallback.update({"item_id": "example-fallback", "importance": 70, "source_rank": 2})
    report["sections"][0]["briefs"].append(fallback)
    index = {
        "items": [
            {
                "item_id": "example-1",
                "title": "Broken high-priority image",
                "url": "https://news.example/high",
                "image_url": "https://cdn.example/broken.png",
            },
            {
                "item_id": "example-fallback",
                "title": "Working fallback image",
                "url": "https://news.example/fallback",
                "image_url": "https://cdn.example/fallback.png",
            },
        ]
    }
    content = _png_bytes()
    stored = _image_record(content)

    def fake_downloader(source_url, _data_dir, _config, **_kwargs):
        if source_url.endswith("broken.png"):
            raise ImageDownloadError("simulated failure")
        return DownloadedImage(
            source_url=source_url,
            resolved_url=source_url,
            local_path=stored["local_path"],
            content_type="image/png",
            sha256=stored["sha256"],
            byte_size=len(content),
            width=3,
            height=2,
            reused=False,
        )

    warnings = materialize_report_images(
        report,
        index,
        tmp_path,
        MediaConfig(max_images_per_report=1),
        downloader=fake_downloader,
    )

    high, fallback = report["sections"][0]["briefs"]
    assert "image" not in high
    assert fallback["image"]["source_url"] == "https://cdn.example/fallback.png"
    assert len(warnings) == 1
    assert report["media_metrics"] == {
        "candidates": 2,
        "attached": 1,
        "unique_files": 1,
        "reused_files": 0,
        "failed": 1,
        "skipped_budget": 0,
        "total_bytes": len(content),
    }


def test_materialize_report_images_uses_fallback_for_the_same_story(
    tmp_path: Path,
):
    report = _report()
    index = {
        "items": [
            {
                "item_id": "example-1",
                "title": "Story with a working secondary image",
                "url": "https://news.example/story",
                "image_url": "https://cdn.example/broken.png",
                "metadata": {
                    "image_candidates": [
                        "https://cdn.example/broken.png",
                        "https://cdn.example/working.png",
                    ]
                },
            }
        ]
    }
    content = _png_bytes()
    stored = _image_record(content)
    requested: list[str] = []

    def fake_downloader(source_url, _data_dir, _config, **_kwargs):
        requested.append(source_url)
        if source_url.endswith("broken.png"):
            raise ImageDownloadError("simulated failure")
        return DownloadedImage(
            source_url=source_url,
            resolved_url=source_url,
            local_path=stored["local_path"],
            content_type="image/png",
            sha256=stored["sha256"],
            byte_size=len(content),
            width=3,
            height=2,
            reused=False,
        )

    warnings = materialize_report_images(
        report,
        index,
        tmp_path,
        MediaConfig(),
        downloader=fake_downloader,
    )

    assert requested == [
        "https://cdn.example/broken.png",
        "https://cdn.example/working.png",
    ]
    assert warnings == []
    assert (
        report["sections"][0]["briefs"][0]["image"]["source_url"]
        == "https://cdn.example/working.png"
    )
    assert report["media_metrics"]["failed"] == 0


def test_materialize_report_images_reuses_persistent_url_cache_without_network(
    tmp_path: Path,
):
    report = _report()
    source_url = "https://cdn.example/story.png"
    index = {
        "items": [
            {
                "item_id": "example-1",
                "title": "Cached story image",
                "url": "https://news.example/story",
                "source_name": "Example News",
                "image_url": source_url,
            }
        ]
    }
    content = _png_bytes()
    stored = _image_record(content, source_url)
    image_path = tmp_path / stored["local_path"]
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(content)
    write_json(
        tmp_path / "media" / "image-cache.json",
        {
            "schema_version": "1.1",
            "updated_at": "2099-01-01T00:00:00+00:00",
            "entries": {
                source_url: {
                    "status": "success",
                    "checked_at": "2099-01-01T00:00:00+00:00",
                    **{
                        key: stored[key]
                        for key in (
                            "resolved_url",
                            "local_path",
                            "content_type",
                            "sha256",
                            "byte_size",
                            "width",
                            "height",
                        )
                    },
                }
            },
        },
    )

    warnings = materialize_report_images(report, index, tmp_path, MediaConfig())

    assert warnings == []
    assert report["sections"][0]["briefs"][0]["image"]["sha256"] == stored["sha256"]
    assert report["media_metrics"]["reused_files"] == 1


def test_image_cache_discards_entries_from_pre_quality_schema(tmp_path: Path):
    write_json(
        tmp_path / "media" / "image-cache.json",
        {
            "schema_version": "1.0",
            "updated_at": "2099-01-01T00:00:00+00:00",
            "entries": {
                "https://cdn.example/old.png": {
                    "status": "success",
                    "checked_at": "2099-01-01T00:00:00+00:00",
                }
            },
        },
    )

    cache = media_module._load_image_cache(tmp_path)

    assert cache["schema_version"] == "1.1"
    assert cache["entries"] == {}


def test_materialize_report_images_respects_negative_cache_retry_window(
    tmp_path: Path,
):
    report = _report()
    source_url = "https://cdn.example/broken.png"
    index = {
        "items": [
            {
                "item_id": "example-1",
                "title": "Broken image",
                "url": "https://news.example/story",
                "image_url": source_url,
            }
        ]
    }
    write_json(
        tmp_path / "media" / "image-cache.json",
        {
            "schema_version": "1.1",
            "updated_at": "2099-01-01T00:00:00+00:00",
            "entries": {
                source_url: {
                    "status": "failed",
                    "checked_at": "2099-01-01T00:00:00+00:00",
                    "retry_after": "2099-01-01T01:00:00+00:00",
                    "error": "ImageDownloadError: invalid raster",
                }
            },
        },
    )

    warnings = materialize_report_images(report, index, tmp_path, MediaConfig())

    assert len(warnings) == 1
    assert "cached image failure" in warnings[0]
    assert "image" not in report["sections"][0]["briefs"][0]
    assert report["media_metrics"]["failed"] == 1


def test_materialized_image_matches_the_report_schema(tmp_path: Path):
    report = _report()
    report.update({"language": "zh-CN", "source_count": 1, "event_count": 0})
    content = _png_bytes()
    stored = _image_record(content)
    index = {
        "items": [
            {
                "item_id": "example-1",
                "title": report["sections"][0]["briefs"][0]["title"],
                "url": "https://news.example/story",
                "source_name": "Example News",
                "image_url": stored["source_url"],
            }
        ]
    }

    def fake_downloader(source_url, _data_dir, _config, **_kwargs):
        return DownloadedImage(
            source_url=source_url,
            resolved_url=source_url,
            local_path=stored["local_path"],
            content_type=stored["content_type"],
            sha256=stored["sha256"],
            byte_size=stored["byte_size"],
            width=stored["width"],
            height=stored["height"],
            reused=False,
        )

    materialize_report_images(
        report,
        index,
        tmp_path,
        MediaConfig(),
        downloader=fake_downloader,
    )
    schema = read_json(Path(__file__).resolve().parents[1] / "schemas" / "report.schema.json")

    jsonschema.Draft202012Validator(schema).validate(report)


def test_local_and_notion_projections_render_a_vertical_uploaded_image_stream():
    image = _image_record(_png_bytes())
    report = _report(image)

    markdown = render_report_markdown(report, media_path_prefix="../..")
    html = render_report_html(report, media_path_prefix="../..")
    blocks = report_to_blocks(report, {image["sha256"]: "upload-123"})

    assert f"../../{image['local_path']}" in markdown
    assert markdown.index("![公开新闻配图]") < markdown.index(
        "A sufficiently detailed public news headline"
    )
    assert f'../../{image["local_path"]}' in html
    assert 'class="brief has-image"' in html
    assert html.index('class="brief-heading"') < html.index("<figure>")
    assert "grid-template-columns:38px minmax(220px,300px)" in html
    story = next(block for block in blocks if block["type"] == "numbered_list_item")
    children = story["numbered_list_item"]["children"]
    assert children[0]["type"] == "image"
    assert children[0]["image"] == {
        "type": "file_upload",
        "file_upload": {"id": "upload-123"},
        "caption": [
            {
                "type": "text",
                "text": {"content": "公开新闻配图｜来源：Example News"},
            }
        ],
    }
    rendered = str(children)
    assert "发布时间" in rendered
    assert "TL;DR" in rendered


def test_checked_in_html_examples_do_not_publish_placeholder_images():
    root = Path(__file__).resolve().parents[1]

    for report_path in (root / "examples" / "reports").glob("*.html"):
        html = report_path.read_text(encoding="utf-8").casefold()
        assert "grey-placeholder" not in html
        assert "gray-placeholder" not in html


def test_notion_image_uploads_are_reused_after_registry_checkpoint(tmp_path: Path):
    content = _png_bytes()
    image = _image_record(content)
    path = tmp_path / image["local_path"]
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    report = _report(image)
    entry: dict[str, Any] = {}
    checkpoints: list[int] = []

    class FakePublisher:
        uploads = 0

        def retrieve_file_upload(self, file_upload_id):
            return {"id": file_upload_id, "status": "uploaded"}

        def upload_file(self, upload_path, content_type):
            assert upload_path == path
            assert content_type == "image/png"
            self.uploads += 1
            return "upload-123"

    publisher = FakePublisher()
    first, first_errors = _prepare_image_uploads(
        publisher,
        report,
        tmp_path,
        entry,
        lambda: checkpoints.append(1),
    )
    second, second_errors = _prepare_image_uploads(
        publisher,
        report,
        tmp_path,
        entry,
        lambda: checkpoints.append(2),
    )

    assert first == second == {image["sha256"]: "upload-123"}
    assert first_errors == second_errors == {}
    assert publisher.uploads == 1
    assert entry["image_uploads"][image["sha256"]]["id"] == "upload-123"
    assert checkpoints == [1]


def test_notion_image_upload_failure_keeps_an_external_visual_fallback(tmp_path: Path):
    content = _png_bytes()
    image = _image_record(content)
    path = tmp_path / image["local_path"]
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    report = _report(image)
    entry: dict[str, Any] = {}

    class FailingPublisher:
        def upload_file(self, _path, _content_type):
            raise RuntimeError("simulated upload failure")

    uploads, errors = _prepare_image_uploads(
        FailingPublisher(),
        report,
        tmp_path,
        entry,
        lambda: None,
    )
    blocks = report_to_blocks(report, uploads)
    story = next(block for block in blocks if block["type"] == "numbered_list_item")
    image_block = story["numbered_list_item"]["children"][0]

    assert uploads == {}
    assert image["sha256"] in errors
    assert image_block["type"] == "image"
    assert image_block["image"]["type"] == "external"
    assert image_block["image"]["external"]["url"] == image["source_url"]


def test_notion_image_backfill_is_in_place_and_idempotent(monkeypatch, tmp_path: Path):
    content = _png_bytes()
    image = _image_record(content)
    image_path = tmp_path / image["local_path"]
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(content)
    report = _report(image)
    report_path = tmp_path / "reports" / "2026-07-23" / "morning-r2.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False),
        encoding="utf-8",
    )
    registry_path = tmp_path / "publishing" / "notion-registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "2026-07-23:morning": {
                    "page_id": "page-1",
                    "report_id": "daily-2026-07-23-morning-r1",
                    "revision": 1,
                    "status": "complete",
                    "image_uploads": {},
                }
            }
        ),
        encoding="utf-8",
    )

    class FakePublisher:
        has_image = False
        appended: list[tuple[str, list[dict[str, Any]]]] = []

        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

        def upload_file(self, _path, _content_type):
            return "upload-123"

        def retrieve_file_upload(self, file_upload_id):
            return {"id": file_upload_id, "status": "uploaded"}

        def retrieve_blocks(self, block_id):
            if block_id == "page-1":
                return [
                    {"id": "divider-1", "type": "divider", "divider": {}},
                    {
                        "id": "heading-1",
                        "type": "heading_2",
                        "heading_2": {"rich_text": [{"plain_text": "06:00 早报"}]},
                    },
                    {
                        "id": "story-1",
                        "type": "numbered_list_item",
                        "numbered_list_item": {
                            "rich_text": [
                                {
                                    "plain_text": report["sections"][0]["briefs"][0]["title"],
                                    "href": "https://news.example/story",
                                }
                            ]
                        },
                    },
                ]
            if block_id == "story-1" and self.has_image:
                return [{"id": "image-1", "type": "image", "image": {}}]
            return []

        def append_blocks(self, block_id, blocks, start_block=0, on_progress=None):
            assert start_block == 0
            self.appended.append((block_id, blocks))
            type(self).has_image = True
            if on_progress:
                on_progress(len(blocks))
            return len(blocks)

    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "test-source")
    monkeypatch.setattr("daily_intelligence.notion.NotionPublisher", FakePublisher)
    monkeypatch.setattr(
        "daily_intelligence.notion.validate_report",
        lambda _path: ([], []),
    )

    first = backfill_report_images(report_path, tmp_path)
    second = backfill_report_images(report_path, tmp_path)

    assert first == ("page-1", "images_backfilled")
    assert second == ("page-1", "skipped_already_present")
    assert len(FakePublisher.appended) == 1
    parent_id, blocks = FakePublisher.appended[0]
    assert parent_id == "story-1"
    assert blocks[0]["type"] == "image"
    assert blocks[0]["image"]["file_upload"] == {"id": "upload-123"}
    registry = read_json(registry_path)
    entry = registry["2026-07-23:morning"]
    assert entry["report_id"] == "daily-2026-07-23-morning-r1"
    assert entry["media_report_id"] == report["report_id"]
    assert entry["image_backfill"]["already_present"] == 1


def test_notion_publisher_uses_json_create_then_multipart_send(tmp_path: Path):
    path = tmp_path / "image.png"
    content = _png_bytes()
    path.write_bytes(content)
    calls: list[tuple[str, str, dict[str, Any]]] = []
    publisher = object.__new__(NotionPublisher)

    def fake_request(method: str, api_path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, api_path, kwargs))
        if api_path == "/file_uploads":
            return {"id": "upload-123", "status": "pending"}
        file_tuple = kwargs["files"]["file"]
        assert file_tuple[0] == "image.png"
        assert file_tuple[1].read() == content
        assert file_tuple[2] == "image/png"
        return {"id": "upload-123", "status": "uploaded"}

    publisher._request = fake_request  # type: ignore[method-assign]

    assert publisher.upload_file(path, "image/png") == "upload-123"
    assert calls[0] == (
        "POST",
        "/file_uploads",
        {
            "json": {
                "mode": "single_part",
                "filename": "image.png",
                "content_type": "image/png",
            }
        },
    )
    assert calls[1][0:2] == ("POST", "/file_uploads/upload-123/send")
