from __future__ import annotations

import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup, Tag

from .access import classify_access_text
from .adapters import browser_items_from_rows
from .config import AppConfig, SourceConfig, source_urls
from .image_policy import is_placeholder_image_url, srcset_candidates
from .models import SourceResult, SourceStatus
from .utils import now_iso

_MAX_HTML_BYTES = 2_000_000
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Edg/131.0 Safari/537.36"
)


def html_index_rows(html: str) -> tuple[str, list[dict[str, Any]]]:
    """处理：从公共 HTML 中惰性提取链接行，不执行任何脚本。
    输入：
    - ``html``：HTTP 或浏览器取得的不可信 HTML 文本；只执行静态解析。
    输出：“从公共 HTML 中惰性提取链接行，不执行任何脚本”得到的固定结构结果；
      返回位置依次对应 title、rows。
    """
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    rows: list[dict[str, Any]] = []
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
        images = list(anchor.select("img"))
        if isinstance(parent, Tag):
            images.extend(parent.select("img"))
        image_candidates: list[str] = []
        seen_images: set[str] = set()
        for image in images:
            raw_candidates = [
                str(image.get(attribute) or "")
                for attribute in (
                    "src",
                    "data-src",
                    "data-original",
                    "data-lazy-src",
                )
            ]
            raw_candidates.extend(srcset_candidates(image.get("srcset")))
            for candidate in raw_candidates:
                candidate = candidate.strip()
                if (
                    not candidate
                    or candidate in seen_images
                    or is_placeholder_image_url(candidate)
                ):
                    continue
                seen_images.add(candidate)
                image_candidates.append(candidate)
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
                "image_url": image_candidates[0] if image_candidates else "",
                "image_candidates": image_candidates,
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
    """处理：用有界 HTTP 请求预取单个来源页面并分类访问状态。
    输入：
    - ``client``：已配置超时、重定向和连接池策略的 HTTP 客户端。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``global_limit``：当前网络阶段共享的全局异步信号量。
    - ``domain_limits``：按主机名懒创建的异步信号量；限制同域并发请求数。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
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
    """处理：按全局和同域并发限制预取全部来源页面。
    输入：
    - ``sources``：本轮选择的来源配置列表；每项定义采集入口、策略和身份信息。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``transport``：测试可注入的 HTTP 传输层；生产通常为空并使用真实网络。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
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
    """处理：按全局与同域并发限制预取来源页面，供后续采集判断是否需要浏览器。
    输入：
    - ``sources``：本轮选择的来源配置列表；每项定义采集入口、策略和身份信息。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
    return asyncio.run(_prefetch_all(sources, config, data_dir))


def page_needs_browser(result: SourceResult | None) -> bool:
    """处理：判断 HTTP 预取之后 Edge 是否仍能增加有效信息。
    输入：
    - ``result``：上游 HTTP 预取或来源采集结果；读取状态、响应类型和页面证据决定后续路径。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
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
