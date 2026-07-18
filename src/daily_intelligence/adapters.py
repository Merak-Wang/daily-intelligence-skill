from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Page

from .config import SourceConfig
from .models import ArticleItem
from .utils import canonicalize_url, clean_title, item_id

IndexAdapter = Callable[[Page, SourceConfig, str], list[ArticleItem]]
_LOW_INFORMATION_TITLES = {
    "abstract",
    "developers",
    "homepage",
    "latest news",
    "learn more",
    "publications",
    "read more",
    "view all comments",
}


def is_eligible(source: SourceConfig, title: str, url: str) -> bool:
    title = clean_title(title)
    if len(title) < 8 or not url.startswith(("http://", "https://")):
        return False
    if title.casefold() in _LOW_INFORMATION_TITLES or not re.search(
        r"[A-Za-z\u3400-\u9fff]", title
    ):
        return False
    if source.exclude_title_patterns and any(
        re.search(pattern, title, re.I) for pattern in source.exclude_title_patterns
    ):
        return False
    hostname = urlsplit(url).netloc.lower().removeprefix("www.")
    if source.include_domains and not any(
        hostname == domain or hostname.endswith(f".{domain}") for domain in source.include_domains
    ):
        return False
    if source.exclude_patterns and any(
        re.search(pattern, url, re.I) for pattern in source.exclude_patterns
    ):
        return False
    return not (
        source.article_patterns
        and not any(re.search(pattern, url, re.I) for pattern in source.article_patterns)
    )


def _article(source: SourceConfig, title: str, url: str, discovered_at: str) -> ArticleItem:
    canonical = canonicalize_url(url)
    return ArticleItem(
        item_id=item_id(source.id, canonical),
        source_id=source.id,
        source_name=source.name,
        title=title,
        url=url,
        canonical_url=canonical,
        discovered_at=discovered_at,
        module=source.module,
        category=source.category,
        metadata={
            "role": source.role,
            "language": source.language,
            "region": source.region,
        },
    )


def _published_at(value: str, url: str, discovered_at: str | None = None) -> str | None:
    text = clean_title(value)
    for candidate in (text, url):
        for pattern in (
            r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b",
            r"\b(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\b",
        ):
            match = re.search(pattern, candidate)
            if not match:
                continue
            try:
                return datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                ).date().isoformat()
            except ValueError:
                continue
    relative = re.search(
        r"\b(\d+)\s*(min(?:ute)?s?|hr|hrs|hour|hours|day|days)\s+ago\b",
        text,
        re.I,
    )
    if relative and discovered_at:
        try:
            observed = datetime.fromisoformat(discovered_at.replace("Z", "+00:00"))
            amount = int(relative.group(1))
            unit = relative.group(2).lower()
            delta = (
                timedelta(minutes=amount)
                if unit.startswith("min")
                else timedelta(hours=amount)
                if unit.startswith(("hr", "hour"))
                else timedelta(days=amount)
            )
            return (observed - delta).isoformat(timespec="seconds")
        except ValueError:
            pass
    for pattern, date_format in (
        (r"\b([A-Z][a-z]{2,8} \d{1,2}, 20\d{2})\b", "%B %d, %Y"),
        (r"\b([A-Z][a-z]{2} \d{1,2}, 20\d{2})\b", "%b %d, %Y"),
        (r"\b(\d{1,2} [A-Z][a-z]{2} 20\d{2})\b", "%d %b %Y"),
    ):
        match = re.search(pattern, text)
        if match:
            try:
                return datetime.strptime(match.group(1), date_format).date().isoformat()
            except ValueError:
                continue
    return None


def collect_browser_index(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    rows = page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(a => ({
            title: (
                (a.querySelector('h1, h2, h3, h4') || {}).innerText ||
                a.getAttribute('aria-label') || a.getAttribute('title') ||
                a.innerText || ''
            ).trim(),
            href: a.href || '',
            context: (() => {
                const card = a.closest('article, li, [class*="card"], [class*="story"]');
                const time = card ? card.querySelector('time') : null;
                return [
                    time ? (time.getAttribute('datetime') || time.textContent || '') : '',
                    card ? (card.textContent || '').slice(0, 500) : '',
                    (a.innerText || '').slice(0, 500)
                ].join(' ');
            })()
        }))"""
    )
    return browser_items_from_rows(rows, source, discovered_at, page.url)


def browser_items_from_rows(
    rows: list[dict[str, str]],
    source: SourceConfig,
    discovered_at: str,
    base_url: str,
) -> list[ArticleItem]:
    """Normalize untrusted index-page rows without executing page instructions."""
    items: list[ArticleItem] = []
    seen: set[str] = set()
    for row in rows:
        title = clean_title(row.get("title", ""))
        url = urljoin(base_url, row.get("href", ""))
        if not is_eligible(source, title, url):
            continue
        article = _article(source, title, url, discovered_at)
        article.published_at = _published_at(row.get("context", ""), url, discovered_at)
        if article.canonical_url in seen:
            continue
        seen.add(article.canonical_url)
        items.append(article)
        if len(items) >= source.max_items:
            break
    return items


def arxiv_items_from_rows(
    rows: list[dict[str, str]],
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    items: list[ArticleItem] = []
    seen: set[str] = set()
    for row in rows:
        title = re.sub(r"^\s*Title:\s*", "", clean_title(row.get("title", "")), flags=re.I)
        url = urljoin(source.url, row.get("href", ""))
        if not is_eligible(source, title, url):
            continue
        article = _article(source, title, url, discovered_at)
        if article.canonical_url in seen:
            continue
        article.published_at = _published_at(
            row.get("date_text", ""), url, discovered_at
        )
        article.description = clean_title(row.get("description", ""))
        seen.add(article.canonical_url)
        items.append(article)
        if len(items) >= source.max_items:
            break
    return items


def collect_arxiv_index(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    rows = page.locator("dl#articles dt, dl dt").evaluate_all(
        """entries => entries.map(dt => {
            const details = dt.nextElementSibling;
            const link = dt.querySelector('a[href*="/abs/"]');
            const titleNode = details ? details.querySelector('.list-title') : null;
            const headings = Array.from(document.querySelectorAll('h3'));
            const dateHeading = headings.reverse().find(
                heading => Boolean(
                    heading.compareDocumentPosition(dt) & Node.DOCUMENT_POSITION_FOLLOWING
                )
            );
            const rawTitle = titleNode ? (titleNode.textContent || '') : '';
            return {
                title: rawTitle.replace(/^\\s*Title:\\s*/i, '').trim(),
                href: link ? link.href : '',
                date_text: dateHeading ? (dateHeading.textContent || '').trim() : ''
            };
        })"""
    )
    return arxiv_items_from_rows(rows, source, discovered_at)


def latest_year_url(rows: list[dict[str, str]]) -> str:
    candidates: list[tuple[int, str]] = []
    for row in rows:
        title = clean_title(row.get("title", ""))
        href = row.get("href", "")
        if re.fullmatch(r"20\d{2}", title) and href.startswith(("http://", "https://")):
            candidates.append((int(title), href))
    if not candidates:
        raise RuntimeError("No year links found on the source index")
    return max(candidates)[1]


def collect_latest_year_index(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    rows = page.locator("a[href]").evaluate_all(
        """anchors => anchors.map(a => ({
            title: (a.innerText || a.getAttribute('title') || '').trim(),
            href: a.href || ''
        }))"""
    )
    year_url = latest_year_url(rows)
    response = page.goto(year_url, wait_until="domcontentloaded", timeout=45_000)
    if response is None or not response.ok:
        status = response.status if response else "no response"
        raise RuntimeError(f"Latest year index returned HTTP {status}")
    return collect_browser_index(page, source, discovered_at)


def twz_items_from_rows(
    rows: list[dict[str, str]],
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    items: list[ArticleItem] = []
    seen: set[str] = set()
    for row in rows:
        title = clean_title(row.get("title", ""))
        url = row.get("href", "")
        if not is_eligible(source, title, url):
            continue
        article = _article(source, title, url, discovered_at)
        if article.canonical_url in seen:
            continue
        posted = re.search(
            r"Posted on ([A-Z][a-z]{2} \d{1,2}, \d{4})",
            row.get("card_text", ""),
        )
        if posted:
            article.published_at = (
                datetime.strptime(posted.group(1), "%b %d, %Y").date().isoformat()
            )
        else:
            article.published_at = _published_at(
                row.get("card_text", ""), url, discovered_at
            )
        article.description = clean_title(row.get("description", ""))
        seen.add(article.canonical_url)
        items.append(article)
        if len(items) >= source.max_items:
            break
    return _clear_misattributed_descriptions(items)


def collect_twz_index(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    rows = page.locator("h3").evaluate_all(
        """headings => headings.map(heading => {
            const link = heading.closest('a[href]');
            const card = heading.closest('.card-post') || heading.closest('article');
            const descriptionNode = card ? card.querySelector('.card-post-dek') : null;
            const timeNode = card ? card.querySelector(
                'time, .byline-item-timestamp, [class*="timestamp"], [class*="date"]'
            ) : null;
            const timeText = timeNode
                ? `${timeNode.getAttribute('datetime') || ''} ${timeNode.textContent || ''}`
                : '';
            return {
                title: (heading.innerText || '').trim(),
                href: link ? link.href : '',
                card_text: timeText.trim(),
                description: descriptionNode ? (descriptionNode.textContent || '').trim() : ''
            };
        })"""
    )
    return twz_items_from_rows(rows, source, discovered_at)


def collect_weibo_api(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    body = page.locator("body").inner_text(timeout=10000)
    payload = json.loads(body)
    if payload.get("ok") != 1:
        raise RuntimeError(f"Weibo API returned ok={payload.get('ok')!r}")
    cards = payload.get("data", {}).get("cards", [])
    group = cards[0].get("card_group", []) if cards else []
    items: list[ArticleItem] = []
    for list_position, row in enumerate(group, start=1):
        title = clean_title(row.get("desc", ""))
        url = row.get("scheme", "")
        if not title or not url or row.get("promotion"):
            continue
        hot_raw = row.get("desc_extr", "")
        hot_value = None
        hot_label = ""
        if isinstance(hot_raw, int):
            hot_value = hot_raw
        elif isinstance(hot_raw, str):
            match = re.search(r"(\d+)$", hot_raw.strip())
            if match:
                hot_value = int(match.group(1))
                hot_label = hot_raw[: match.start()].strip()
        article = _article(source, title, url, discovered_at)
        article.metadata.update(
            {
                "list_position": list_position,
                "hot_rank": row.get("realpos") or row.get("band_rank"),
                "hot_value": hot_value,
                "hot_label": hot_label,
                "is_pinned": not bool(row.get("realpos") or row.get("band_rank")),
            }
        )
        items.append(article)
        if len(items) >= source.max_items:
            break
    return items


def seed_items_from_payload(
    payload: dict,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    items: list[ArticleItem] = []
    for row in payload.get("sub_article_list", []):
        metadata = row.get("ArticleMeta", {})
        content_zh = row.get("ArticleSubContentZh", {})
        content_en = row.get("ArticleSubContentEn", {})
        content = content_zh if content_zh.get("Title") else content_en
        title = clean_title(content.get("Title", ""))
        links = metadata.get("ExternalLinks", [])
        url = next((link.get("Link") for link in links if link.get("Link")), "")
        if "/pdf/" in url:
            url = url.replace("/pdf/", "/abs/").removesuffix(".pdf")
        if not title or not url:
            continue
        article = _article(source, title, url, discovered_at)
        article.description = content.get("Abstract", "")
        publish_ms = metadata.get("PublishDate")
        if isinstance(publish_ms, (int, float)):
            article.published_at = datetime.fromtimestamp(publish_ms / 1000, UTC).isoformat()
        article.original_provider = "ByteDance Seed"
        article.metadata.update(
            {
                "article_id": metadata.get("ArticleID"),
                "authors": metadata.get("Author", ""),
                "journal": metadata.get("Journal", ""),
                "research_areas": [
                    area.get("ResearchAreaNameZh") or area.get("ResearchAreaName")
                    for area in metadata.get("ResearchArea", [])
                ],
            }
        )
        items.append(article)
        if len(items) >= source.max_items:
            break
    return items


def collect_seed_papers(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    with page.expect_response(
        lambda response: "get_article_list_v2" in response.url,
        timeout=30_000,
    ) as response_info:
        page.reload(wait_until="domcontentloaded", timeout=45_000)
    response = response_info.value
    if not response.ok:
        raise RuntimeError(f"ByteDance Seed papers API returned HTTP {response.status}")
    return seed_items_from_payload(response.json(), source, discovered_at)


_ADAPTERS: dict[str, IndexAdapter] = {
    "arxiv_index": collect_arxiv_index,
    "browser_index": collect_browser_index,
    "latest_year_index": collect_latest_year_index,
    "seed_papers": collect_seed_papers,
    "twz_index": collect_twz_index,
    "weibo_api": collect_weibo_api,
}


def register_adapter(name: str, adapter: IndexAdapter) -> None:
    if name in _ADAPTERS:
        raise ValueError(f"Adapter already registered: {name}")
    _ADAPTERS[name] = adapter


def collect_candidates(
    page: Page,
    source: SourceConfig,
    discovered_at: str,
) -> list[ArticleItem]:
    try:
        adapter = _ADAPTERS[source.adapter_name]
    except KeyError as exc:
        available = ", ".join(sorted(_ADAPTERS))
        raise ValueError(
            f"Unknown source adapter {source.adapter_name!r}; available: {available}"
        ) from exc
    return _clear_misattributed_descriptions(adapter(page, source, discovered_at))


def _clear_misattributed_descriptions(items: list[ArticleItem]) -> list[ArticleItem]:
    """Drop long descriptions duplicated across distinct links instead of misattributing them."""
    counts: dict[str, int] = {}
    for item in items:
        description = clean_title(item.description)
        if len(description) >= 60:
            key = description.casefold()
            counts[key] = counts.get(key, 0) + 1
    duplicated = {key for key, count in counts.items() if count > 1}
    if not duplicated:
        return items
    for item in items:
        if clean_title(item.description).casefold() in duplicated:
            item.description = ""
    return items
