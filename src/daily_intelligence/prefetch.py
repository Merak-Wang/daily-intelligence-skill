from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from .access import classify_access_text
from .adapters import browser_items_from_rows
from .config import AppConfig, SourceConfig, source_urls
from .models import SourceResult, SourceStatus
from .utils import now_iso

_MAX_HTML_BYTES = 2_000_000
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Edg/131.0 Safari/537.36"
)


def html_index_rows(html: str) -> tuple[str, list[dict[str, str]]]:
    """Extract inert link rows from public HTML without executing scripts."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    rows: list[dict[str, str]] = []
    for anchor in soup.select("a[href]"):
        heading = anchor.select_one("h1, h2, h3, h4")
        anchor_title = (
            heading.get_text(" ", strip=True)
            if heading
            else anchor.get("aria-label")
            or anchor.get("title")
            or anchor.get_text(" ", strip=True)
        )
        parent = anchor.find_parent(
            lambda tag: isinstance(tag, Tag)
            and (
                tag.name in {"article", "li"}
                or any(
                    marker in " ".join(tag.get("class", [])).lower()
                    for marker in ("card", "story")
                )
            )
        )
        time = parent.select_one("time") if isinstance(parent, Tag) else None
        context = " ".join(
            part
            for part in (
                (
                    str(time.get("datetime") or time.get_text(" ", strip=True))
                    if time
                    else ""
                ),
                parent.get_text(" ", strip=True)[:500]
                if isinstance(parent, Tag)
                else "",
                anchor.get_text(" ", strip=True)[:500],
            )
            if part
        )
        rows.append(
            {
                "title": str(anchor_title or ""),
                "href": str(anchor.get("href") or ""),
                "context": context,
            }
        )
    return title, rows


async def _prefetch_one(
    client: httpx.AsyncClient,
    source: SourceConfig,
    url: str,
    config: AppConfig,
    global_limit: asyncio.Semaphore,
    domain_limits: defaultdict[str, asyncio.Semaphore],
) -> SourceResult:
    collected_at = now_iso(config.timezone)
    domain = urlsplit(url).netloc.lower()
    async with global_limit, domain_limits[domain]:
        try:
            response = await client.get(url)
        except Exception as exc:
            return SourceResult(
                source_id=source.id,
                source_name=source.name,
                source_url=url,
                status=SourceStatus.FAILED,
                collected_at=collected_at,
                module=source.module,
                category=source.category,
                error=f"HTTP prefetch {type(exc).__name__}: {exc}",
                challenge={"prefetch_error": True},
            )

    encoding = response.encoding or "utf-8"
    body = response.content[:_MAX_HTML_BYTES].decode(encoding, errors="replace")
    title, rows = html_index_rows(body)
    challenge = classify_access_text(response.status_code, title, body[:30000])
    if challenge["rate_limited"]:
        status = SourceStatus.RATE_LIMITED
        items = []
    elif challenge["required"]:
        status = SourceStatus.VERIFICATION_REQUIRED
        items = []
    elif response.status_code >= 400:
        status = SourceStatus.FAILED
        items = []
    else:
        items = browser_items_from_rows(rows, source, collected_at, str(response.url))
        status = SourceStatus.SUCCESS if items else SourceStatus.NO_ITEMS
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=url,
        status=status,
        collected_at=collected_at,
        module=source.module,
        category=source.category,
        page_title=title,
        final_url=str(response.url),
        http_status=response.status_code,
        error=f"HTTP {response.status_code}" if response.status_code >= 400 else None,
        challenge=challenge,
        items=items,
    )


async def _prefetch_all(
    sources: list[SourceConfig],
    config: AppConfig,
    data_dir: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[tuple[str, str], SourceResult]:
    global_limit = asyncio.Semaphore(
        max(1, config.browser.collection_global_concurrency)
    )
    per_domain = max(1, config.browser.collection_per_domain_concurrency)
    domain_limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(per_domain)
    )
    timeout = max(1, config.browser.http_prefetch_timeout_ms) / 1000
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers={"User-Agent": _USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        transport=transport,
    ) as client:
        jobs = [
            (source, url)
            for source in sources
            if source.adapter_name == "browser_index"
            for url in source_urls(source, data_dir)
        ]
        results = await asyncio.gather(
            *(
                _prefetch_one(
                    client,
                    source,
                    url,
                    config,
                    global_limit,
                    domain_limits,
                )
                for source, url in jobs
            )
        )
    return {
        (source.id, url): result
        for (source, url), result in zip(jobs, results, strict=True)
    }


def prefetch_browser_pages(
    sources: list[SourceConfig],
    config: AppConfig,
    data_dir: Path,
) -> dict[tuple[str, str], SourceResult]:
    return asyncio.run(_prefetch_all(sources, config, data_dir))


def page_needs_browser(result: SourceResult | None) -> bool:
    """Decide whether Edge can add value after an HTTP prefetch attempt."""
    if result is None:
        return True
    status = SourceStatus(result.status)
    if status == SourceStatus.RATE_LIMITED:
        return False
    if status == SourceStatus.SUCCESS and result.items:
        return False
    return not (
        status == SourceStatus.FAILED
        and result.http_status not in {None, 401, 403}
    )
