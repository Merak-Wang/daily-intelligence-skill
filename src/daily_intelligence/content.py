from __future__ import annotations

import asyncio
import contextlib
import html
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, async_playwright

from .access import classify_access_text
from .collector import CHALLENGE_TEXTS
from .config import AppConfig, SourceConfig, resolve_browser_channel, resolve_profile_dir
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
_MAX_CONTENT_BYTES = 4 * 1024 * 1024
_HTTP_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Edg/131.0 Safari/537.36"
)


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


def _html_meta(soup: BeautifulSoup, selectors: list[str]) -> str:
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        value = node.get("content") or node.get("datetime") or node.get_text(" ", strip=True)
        if value:
            return str(value).strip()
    return ""


def _static_visible_text(
    soup: BeautifulSoup,
    selectors: list[str],
) -> tuple[str, str | None]:
    for node in soup.select(",".join(NOISE_SELECTORS)):
        node.decompose()
    best_text = ""
    best_selector = None
    for selector in [*selectors, "article", "main", "body"]:
        try:
            nodes = soup.select(selector)
        except Exception:
            continue
        for node in nodes:
            text = " ".join(node.get_text("\n", strip=True).split())
            if len(text) > len(best_text):
                best_text = text
                best_selector = selector
            if len(text) >= 1500:
                return text, selector
    return best_text, best_selector


def _content_output_path(
    data_dir: Path,
    source_id: str,
    item_id: object,
    timezone: str,
) -> Path:
    return (
        data_dir
        / "content"
        / source_id
        / str(item_id)
        / f"{timestamp_slug(timezone)}.md"
    )


def _apply_http_document(
    item: dict[str, Any],
    source: SourceConfig,
    body_html: str,
    final_url: str,
    http_status: int,
    config: AppConfig,
    data_dir: Path,
) -> bool:
    """Apply inert HTTP content and return whether a browser can still add value."""
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    soup = BeautifulSoup(body_html, "html.parser")
    page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
    challenge = classify_access_text(http_status, page_title, body_html[:30000])
    metadata["content_http_status"] = http_status
    metadata["content_http_final_url"] = final_url
    metadata["content_acquisition"] = "http"
    if challenge["required"] or challenge["rate_limited"]:
        item["content_status"] = ContentStatus.VERIFICATION_REQUIRED
        metadata["content_challenge"] = challenge
        return False
    if http_status >= 400:
        item["content_status"] = ContentStatus.FAILED
        metadata["content_error"] = f"HTTP {http_status}"
        return False

    title = _html_meta(
        soup,
        ['meta[property="og:title"]', 'meta[name="twitter:title"]'],
    )
    if title:
        item["title"] = html.unescape(title)
    description = _html_meta(
        soup,
        ['meta[name="description"]', 'meta[property="og:description"]'],
    )
    if description:
        item["description"] = html.unescape(description)
    published = _html_meta(
        soup,
        [
            'meta[property="article:published_time"]',
            'meta[name="article:published_time"]',
            "time[datetime]",
        ],
    )
    if published:
        item["published_at"] = published
    image_url = _html_meta(
        soup,
        ['meta[property="og:image"]', 'meta[name="twitter:image"]'],
    )
    resolved_image_url = urljoin(final_url, image_url) if image_url else ""
    if resolved_image_url.startswith(("http://", "https://")):
        item["image_url"] = resolved_image_url

    body, selector = _static_visible_text(soup, source.content_selectors)
    if len(body) >= 1500:
        status = ContentStatus.FULL_TEXT
    elif len(body) >= 500:
        status = ContentStatus.PARTIAL
    else:
        item["content_status"] = ContentStatus.METADATA_ONLY
        item["content_characters"] = len(body)
        metadata["content_selector"] = selector
        return True
    item["content_status"] = status
    item["content_characters"] = len(body)
    metadata["content_selector"] = selector
    output = _content_output_path(
        data_dir,
        source.id,
        item.get("item_id"),
        config.timezone,
    )
    item["content_path"] = str(output)
    save_markdown(output, item, body, now_iso(config.timezone))
    return False


async def _read_bounded_html(
    response: httpx.Response,
    max_bytes: int = _MAX_CONTENT_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            allowed = len(chunk) - (total - max_bytes)
            if allowed > 0:
                chunks.append(chunk[:allowed])
            break
        chunks.append(chunk)
    return b"".join(chunks)


async def _extract_http_one(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    config: AppConfig,
    data_dir: Path,
) -> bool:
    """Try a bounded no-script fetch; return True only when Edge may add value."""
    source = config.source_by_id(str(item["source_id"]))
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    try:
        async with client.stream("GET", str(item["url"])) as response:
            content_type = response.headers.get("content-type", "").casefold()
            if content_type and not any(
                marker in content_type
                for marker in ("text/html", "application/xhtml+xml", "text/plain")
            ):
                item["content_status"] = ContentStatus.METADATA_ONLY
                metadata["content_http_status"] = response.status_code
                metadata["content_error"] = (
                    f"unsupported content type {content_type.split(';', 1)[0]}"
                )
                metadata["content_acquisition"] = "http"
                return False
            content = await _read_bounded_html(response)
            encoding = response.encoding or "utf-8"
            body_html = content.decode(encoding, errors="replace")
            return _apply_http_document(
                item,
                source,
                body_html,
                str(response.url),
                response.status_code,
                config,
                data_dir,
            )
    except (httpx.HTTPError, UnicodeError) as exc:
        item["content_status"] = ContentStatus.FAILED
        metadata["content_http_error"] = f"{type(exc).__name__}: {exc}"
        metadata["content_acquisition"] = "http_failed"
        return True


async def _run_http_extraction(
    targets: list[dict[str, Any]],
    config: AppConfig,
    data_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[dict[str, Any]]:
    global_limit = asyncio.Semaphore(
        max(1, config.browser.collection_global_concurrency)
    )
    per_domain = max(1, config.browser.collection_per_domain_concurrency)
    domain_semaphores: dict[str, asyncio.Semaphore] = {}

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=max(1, config.browser.http_prefetch_timeout_ms) / 1000,
        headers={
            "User-Agent": _HTTP_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
        },
        transport=transport,
    ) as client:

        async def guarded(item: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            domain = _domain_key(str(item.get("url", "")))
            domain_limit = domain_semaphores.setdefault(
                domain,
                asyncio.Semaphore(per_domain),
            )
            async with domain_limit, global_limit:
                return item, await _extract_http_one(client, item, config, data_dir)

        results = await asyncio.gather(*(guarded(item) for item in targets))
    return [item for item, needs_browser in results if needs_browser]


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
        wait_ms = source.wait_ms or min(config.browser.default_wait_ms, 1000)
        with contextlib.suppress(Exception):
            await page.locator(",".join(source.content_selectors)).first.wait_for(
                state="attached",
                timeout=wait_ms,
            )
        http_status = response.status if response else None
        metadata["content_acquisition"] = "browser"
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
        resolved_image_url = urljoin(page.url, image_url) if image_url else ""
        if resolved_image_url.startswith(("http://", "https://")):
            item["image_url"] = resolved_image_url
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
            output = _content_output_path(
                data_dir,
                source.id,
                item.get("item_id"),
                config.timezone,
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


def _has_reusable_content(item: dict[str, Any], data_dir: Path) -> bool:
    if item.get("content_status") not in {
        ContentStatus.FULL_TEXT,
        ContentStatus.PARTIAL,
    }:
        return False
    value = item.get("content_path")
    if not value:
        return False
    path = Path(str(value))
    candidate = path if path.is_absolute() else data_dir / path
    try:
        candidate.resolve().relative_to(data_dir.resolve())
    except ValueError:
        return False
    return candidate.is_file()


async def _extract_pipeline(
    targets: list[dict[str, Any]],
    config: AppConfig,
    data_dir: Path,
    headed: bool,
    profile: Path,
    channel: str | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    reusable = [item for item in targets if _has_reusable_content(item, data_dir)]
    for item in reusable:
        metadata = item.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["content_acquisition"] = "cache"
    reusable_ids = {id(item) for item in reusable}
    pending = [item for item in targets if id(item) not in reusable_ids]

    http_started = time.perf_counter()
    browser_targets = (
        await _run_http_extraction(pending, config, data_dir) if pending else []
    )
    http_seconds = time.perf_counter() - http_started
    browser_started = time.perf_counter()
    if browser_targets:
        await _extract_with_browser(
            browser_targets,
            config,
            data_dir,
            headed,
            profile,
            channel,
        )
    browser_seconds = time.perf_counter() - browser_started
    return {
        "selected": len(targets),
        "cache_hits": len(reusable),
        "http_attempted": len(pending),
        "http_successful": sum(
            item.get("metadata", {}).get("content_acquisition") == "http"
            and item.get("content_status")
            in {ContentStatus.FULL_TEXT, ContentStatus.PARTIAL}
            for item in pending
            if isinstance(item.get("metadata"), dict)
        ),
        "browser_fallback": len(browser_targets),
        "successful": sum(
            item.get("content_status")
            in {ContentStatus.FULL_TEXT, ContentStatus.PARTIAL}
            and bool(item.get("content_path"))
            for item in targets
        ),
        "http_seconds": round(http_seconds, 3),
        "browser_seconds": round(browser_seconds, 3),
        "total_seconds": round(time.perf_counter() - started, 3),
    }


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
    content_metrics = asyncio.run(
        _extract_pipeline(
            targets,
            config,
            data_dir,
            headed,
            profile,
            channel,
        )
    )
    synchronize_nested_items(payload)
    payload["content_updated_at"] = now_iso(config.timezone)
    payload["content_metrics"] = content_metrics
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
