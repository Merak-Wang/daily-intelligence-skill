from __future__ import annotations

import asyncio
import contextlib
import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Page, async_playwright

from .collector import CHALLENGE_TEXTS
from .config import AppConfig, resolve_browser_channel, resolve_profile_dir
from .models import ContentStatus
from .storage import next_revision, write_immutable_json, write_text_atomic
from .utils import now_iso, read_json, timestamp_slug, write_json

NOISE_SELECTORS = [
    "script",
    "style",
    "noscript",
    "nav",
    "footer",
    "aside",
    '[aria-label*="advert" i]',
    '[class*="advert" i]',
    '[class*="cookie" i]',
    '[class*="newsletter" i]',
]


def synchronize_nested_items(payload: dict[str, Any]) -> None:
    """Keep the legacy nested view consistent with the canonical root items array."""
    root_items = {
        item.get("item_id"): item
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    for source in payload.get("sources", []):
        if not isinstance(source, dict):
            continue
        for nested in source.get("items", []):
            if not isinstance(nested, dict):
                continue
            canonical = root_items.get(nested.get("item_id"))
            if canonical is not None:
                nested.clear()
                nested.update(canonical)


async def meta_content(page: Page, selectors: list[str]) -> str:
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count():
            value = await locator.first.get_attribute("content")
            value = value or await locator.first.get_attribute("datetime")
            if value:
                return value.strip()
    return ""


async def extract_visible_text(page: Page, selectors: list[str]) -> tuple[str, str | None]:
    for selector in selectors:
        locator = page.locator(selector)
        if not await locator.count():
            continue
        try:
            text = (await locator.first.inner_text(timeout=5000)).strip()
        except Exception:
            continue
        if len(text) >= 500:
            return text, selector
    return "", None


def save_markdown(path: Path, item: dict[str, Any], body: str, retrieved_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "title": item.get("title", ""),
        "source": item.get("source_name", ""),
        "url": item.get("url", ""),
        "retrieved_at": retrieved_at,
        "content_status": item.get("content_status", ""),
    }
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.extend(["---", "", body.strip(), ""])
    write_text_atomic(path, "\n".join(lines))


async def detect_challenge(page: Page, http_status: int | None) -> dict[str, Any]:
    title = ""
    body = ""
    with contextlib.suppress(Exception):
        title = (await page.title()).lower()
    with contextlib.suppress(Exception):
        body = (await page.locator("body").inner_text(timeout=3000)).lower()[:30000]
    matched = next((text for text in CHALLENGE_TEXTS if text in title or text in body), None)
    iframe_count = 0
    with contextlib.suppress(Exception):
        iframe_count = await page.locator(
            'iframe[src*="captcha"], iframe[src*="challenge"], iframe[title*="challenge" i]'
        ).count()
    return {
        "required": http_status in {401, 403, 429} or matched is not None or iframe_count > 0,
        "matched_text": matched,
        "iframe_detected": iframe_count > 0,
    }


def _ordered_targets(
    items: list[dict[str, Any]],
    selected_ids: list[str],
    max_items: int,
) -> list[dict[str, Any]]:
    """Keep the caller's importance order and ignore duplicate or unknown IDs."""
    by_id = {
        str(item.get("item_id")): item
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }
    ordered_ids = dict.fromkeys(selected_ids)
    return [by_id[item_id] for item_id in ordered_ids if item_id in by_id][:max_items]


def _domain_key(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.") or "unknown"


async def _extract_one(
    context: BrowserContext,
    item: dict[str, Any],
    config: AppConfig,
    data_dir: Path,
) -> None:
    source = config.source_by_id(str(item["source_id"]))
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    page = await context.new_page()
    response = None
    try:
        response = await page.goto(
            str(item["url"]),
            wait_until="domcontentloaded",
            timeout=config.browser.navigation_timeout_ms,
        )
        await page.wait_for_timeout(source.wait_ms or config.browser.default_wait_ms)
        http_status = response.status if response else None
        challenge = await detect_challenge(page, http_status)
        if challenge["required"]:
            item["content_status"] = ContentStatus.VERIFICATION_REQUIRED
            metadata["content_challenge"] = challenge
            return
        if http_status is not None and http_status >= 400:
            item["content_status"] = ContentStatus.FAILED
            metadata["content_http_status"] = http_status
            metadata["content_error"] = f"HTTP {http_status}"
            return
        with contextlib.suppress(Exception):
            await page.locator(",".join(NOISE_SELECTORS)).evaluate_all(
                "nodes => nodes.forEach(n => n.remove())"
            )
        title = await meta_content(
            page, ['meta[property="og:title"]', 'meta[name="twitter:title"]']
        )
        if title:
            item["title"] = html.unescape(title)
        description = await meta_content(
            page,
            ['meta[name="description"]', 'meta[property="og:description"]'],
        )
        if description:
            item["description"] = html.unescape(description)
        published = await meta_content(
            page,
            [
                'meta[property="article:published_time"]',
                'meta[name="article:published_time"]',
                "time[datetime]",
            ],
        )
        if published:
            item["published_at"] = published
        image_url = await meta_content(
            page,
            ['meta[property="og:image"]', 'meta[name="twitter:image"]'],
        )
        if image_url.startswith(("http://", "https://")):
            item["image_url"] = image_url
        body, selector = await extract_visible_text(page, source.content_selectors)
        if len(body) >= 1500:
            status = ContentStatus.FULL_TEXT
        elif len(body) >= 500:
            status = ContentStatus.PARTIAL
        else:
            status = ContentStatus.METADATA_ONLY
        item["content_status"] = status
        item["content_characters"] = len(body)
        metadata["content_selector"] = selector
        metadata["content_http_status"] = http_status
        if body:
            output = (
                data_dir
                / "content"
                / source.id
                / str(item["item_id"])
                / f"{timestamp_slug(config.timezone)}.md"
            )
            item["content_path"] = str(output)
            save_markdown(output, item, body, now_iso(config.timezone))
    except Exception as exc:
        item["content_status"] = ContentStatus.FAILED
        metadata["content_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with contextlib.suppress(Exception):
            await page.close()


async def _run_parallel_extraction(
    context: BrowserContext,
    targets: list[dict[str, Any]],
    config: AppConfig,
    data_dir: Path,
) -> None:
    """Run bounded extraction: cross-domain parallelism, same-domain politeness."""
    global_limit = max(1, config.browser.global_concurrency)
    domain_limit = max(1, config.browser.per_domain_concurrency)
    global_semaphore = asyncio.Semaphore(global_limit)
    domain_semaphores: dict[str, asyncio.Semaphore] = {}

    async def guarded(item: dict[str, Any]) -> None:
        domain = _domain_key(str(item.get("url", "")))
        domain_semaphore = domain_semaphores.setdefault(
            domain, asyncio.Semaphore(domain_limit)
        )
        # Acquire the domain slot first so same-domain waiters do not occupy a
        # global slot and block unrelated sources.
        async with domain_semaphore, global_semaphore:
            await _extract_one(context, item, config, data_dir)

    await asyncio.gather(*(guarded(item) for item in targets))


async def _extract_with_browser(
    targets: list[dict[str, Any]],
    config: AppConfig,
    data_dir: Path,
    headed: bool,
    profile: Path,
    channel: str | None,
) -> None:
    async with async_playwright() as playwright:
        kwargs: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": not headed,
            "locale": "en-US",
            "timezone_id": config.timezone,
            "viewport": {"width": 1440, "height": 1000},
        }
        if channel:
            kwargs["channel"] = channel
        context = await playwright.chromium.launch_persistent_context(**kwargs)
        try:
            await _run_parallel_extraction(context, targets, config, data_dir)
        finally:
            await context.close()


def extract_content(
    index_path: Path,
    config: AppConfig,
    data_dir: Path,
    selected_ids: list[str],
    max_items: int | None,
    headed: bool,
    profile_dir: Path | None = None,
    browser_channel: str | None = None,
) -> Path:
    payload = read_json(index_path)
    if not isinstance(payload, dict):
        raise ValueError("Index must be a JSON object")
    items = payload.get("items", [])
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be at least 1")
    effective_limit = min(
        max_items if max_items is not None else config.budget.max_fulltext_per_run,
        config.budget.max_fulltext_per_run,
    )
    targets = _ordered_targets(items, selected_ids, effective_limit)
    if not targets:
        raise ValueError("No selected item IDs were found in the index")
    profile = resolve_profile_dir(config, profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    channel = resolve_browser_channel(config, browser_channel)
    asyncio.run(_extract_with_browser(targets, config, data_dir, headed, profile, channel))
    synchronize_nested_items(payload)
    payload["content_updated_at"] = now_iso(config.timezone)
    payload["derived_from"] = str(index_path.resolve())
    date = str(payload.get("date"))
    edition = str(payload.get("edition"))
    index_dir = data_dir / "indexes" / date
    revision = next_revision(index_dir, edition)
    payload["revision"] = revision
    payload["index_id"] = f"index-{date}-{edition}-r{revision}"
    output = index_dir / f"{edition}-r{revision}.json"
    write_immutable_json(output, payload)
    write_json(data_dir / "indexes" / "latest.json", payload)
    return output
