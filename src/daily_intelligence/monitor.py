from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from .access import classify_access_text
from .clustering import cluster_articles
from .config import AppConfig, SourceConfig
from .feeds import (
    FeedFetchResult,
    article_from_dict,
    discover_feed_urls,
    fetch_feed,
    looks_like_feed,
)
from .models import ArticleItem, SourceResult, SourceStatus
from .prefetch import prefetch_browser_pages
from .utils import read_json_object, write_json

_MONITOR_SOURCE_STATUSES = {
    SourceStatus.SUCCESS,
    SourceStatus.PARTIAL,
    SourceStatus.NO_ITEMS,
    SourceStatus.FAILED,
    SourceStatus.RATE_LIMITED,
    SourceStatus.VERIFICATION_REQUIRED,
    "unsupported",
}


def _safe_read_object(path: Path) -> dict[str, Any]:
    """处理：读取 JSON 对象；文件缺失或损坏时返回空对象。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：“读取 JSON 对象；文件缺失或损坏时返回空对象”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    if not path.exists():
        return {}
    with contextlib.suppress(OSError, ValueError, TypeError):
        return read_json_object(path)
    return {}


def _parse_time(value: object, timezone: str) -> datetime | None:
    """处理：把可选 ISO 时间文本解析为带时区时间，空值或非法值返回 None。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：封装“把可选 ISO 时间文本解析为带时区时间，
      空值或非法值返回 None”业务结果的 ``datetime | None`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _current_time(timezone: str, now: datetime | None = None) -> datetime:
    """处理：把注入或当前时间规范到配置时区。
    输入：
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``now``：可注入的当前时间；测试可固定它，生产为空时读取配置时区时钟。
    输出：封装“把注入或当前时间规范到配置时区”业务结果的 ``datetime`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    current = now or datetime.now(ZoneInfo(timezone))
    if current.tzinfo is None:
        return current.replace(tzinfo=ZoneInfo(timezone))
    return current


def _item_time(item: dict[str, Any], timezone: str) -> datetime | None:
    """处理：从条目发布时间或发现时间解析排序时间。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：封装“从条目发布时间或发现时间解析排序时间”业务结果的 ``datetime | None`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    return _parse_time(item.get("published_at") or item.get("discovered_at"), timezone)


async def _read_bounded_response(response: httpx.Response, max_bytes: int) -> bytes:
    """处理：流式读取发现响应并在配置字节上限处停止。
    输入：
    - ``response``：已建立的 HTTP 流式响应；函数负责读取上限和错误语义。
    - ``max_bytes``：允许读取或下载的最大字节数；达到上限后停止或报错。
    输出：受大小边界约束的字节内容，可直接写入文件或 HTTP 响应。
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"Response exceeds configured {max_bytes}-byte safety limit")
        chunks.append(chunk)
    return b"".join(chunks)


async def _discover_one(
    client: httpx.AsyncClient,
    source: SourceConfig,
    config: AppConfig,
    global_limit: asyncio.Semaphore,
    domain_limits: defaultdict[str, asyncio.Semaphore],
) -> tuple[list[str], str, str | None]:
    """处理：访问来源首页，识别挑战并发现声明或直接返回的 Feed URL。
    输入：
    - ``client``：已配置超时、重定向和连接池策略的 HTTP 客户端。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``global_limit``：当前网络阶段共享的全局异步信号量。
    - ``domain_limits``：按主机名懒创建的异步信号量；限制同域并发请求数。
    输出：“访问来源首页，识别挑战并发现声明或直接返回的 Feed URL”得到的固定结构结果；
      返回位置依次对应 feeds、SourceStatus.SUCCESS if feeds el、None if feeds else 'No RSS or At
      。
    """
    domain = urlsplit(source.url).netloc.lower()
    try:
        async with (
            global_limit,
            domain_limits[domain],
            client.stream(
                "GET",
                source.url,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/rss+xml,"
                        "application/atom+xml;q=0.9"
                    ),
                    "User-Agent": "DailyIntelligenceMonitor/2.0 (+local feed discovery)",
                },
            ) as response,
        ):
            content = await _read_bounded_response(
                response, config.monitor.max_feed_bytes
            )
        if response.status_code >= 400:
            return [], SourceStatus.FAILED, f"HTTP {response.status_code}"
        text = content.decode(response.encoding or "utf-8", errors="replace")
        challenge = classify_access_text(response.status_code, "", text[:30000])
        if challenge.get("rate_limited"):
            return [], SourceStatus.RATE_LIMITED, "Feed discovery was rate limited"
        if challenge.get("required"):
            return (
                [],
                SourceStatus.VERIFICATION_REQUIRED,
                "Feed discovery reached a verification page",
            )
        if looks_like_feed(content, response.headers.get("content-type", "")):
            return [str(response.url)], SourceStatus.SUCCESS, None
        feeds = discover_feed_urls(text, str(response.url))
        return feeds, SourceStatus.SUCCESS if feeds else "unsupported", (
            None if feeds else "No RSS or Atom feed was declared or discovered"
        )
    except Exception as exc:
        return [], SourceStatus.FAILED, f"{type(exc).__name__}: {exc}"


async def _feed_phase(
    sources: list[SourceConfig],
    config: AppConfig,
    data_dir: Path,
    *,
    force: bool,
    checked_at: str,
    transport: httpx.AsyncBaseTransport | None,
) -> tuple[
    dict[str, list[FeedFetchResult]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    """处理：发现、缓存并并发抓取来源 Feed，返回结果和登记表状态。
    输入：
    - ``sources``：本轮选择的来源配置列表；每项定义采集入口、策略和身份信息。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``force``：是否忽略正常缓存或重复保护并显式重新执行允许的步骤。
    - ``checked_at``：本次来源或缓存检查时间；用于刷新计划和健康状态。
    - ``transport``：测试可注入的 HTTP 传输层；生产通常为空并使用真实网络。
    输出：单个 Feed 的抓取结果；包含访问状态、缓存信息、错误、刷新时间和规范文章条目。
    """
    registry_path = data_dir / "monitor" / "feed-registry.json"
    registry = _safe_read_object(registry_path)
    registry_sources = (
        registry.get("sources", {}) if isinstance(registry.get("sources"), dict) else {}
    )
    global_limit = asyncio.Semaphore(config.monitor.global_concurrency)
    per_domain = config.monitor.per_domain_concurrency
    domain_limits: defaultdict[str, asyncio.Semaphore] = defaultdict(
        lambda: asyncio.Semaphore(per_domain)
    )
    timeout = httpx.Timeout(config.monitor.request_timeout_seconds)
    limits = httpx.Limits(
        max_connections=config.monitor.global_concurrency,
        max_keepalive_connections=config.monitor.global_concurrency,
    )
    discovery_statuses: dict[str, dict[str, Any]] = {}
    source_feed_urls: dict[str, list[str]] = {}
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        limits=limits,
        transport=transport,
    ) as client:
        discovery_jobs: list[SourceConfig] = []
        for source in sources:
            configured = list(dict.fromkeys(source.feed_urls))
            if configured:
                # 配置 Feed 是主入口；登记表中的历史发现结果只作为补充并保持稳定顺序。
                cached = registry_sources.get(source.id, {})
                cached_feeds = (
                    cached.get("feed_urls", []) if isinstance(cached, dict) else []
                )
                source_feed_urls[source.id] = list(
                    dict.fromkeys(
                        [
                            *configured,
                            *(
                                str(url)
                                for url in cached_feeds
                                if isinstance(url, str)
                            ),
                        ]
                    )
                )
                continue
            cached = registry_sources.get(source.id, {})
            cached_feeds = (
                cached.get("feed_urls", []) if isinstance(cached, dict) else []
            )
            if cached_feeds and not force:
                # 非强制刷新复用已验证的发现结果，减少对来源首页的额外请求。
                source_feed_urls[source.id] = [
                    str(url) for url in cached_feeds if isinstance(url, str)
                ]
            elif config.monitor.auto_discover_feeds:
                discovery_jobs.append(source)
            else:
                source_feed_urls[source.id] = []

        discovered = await asyncio.gather(
            *(
                _discover_one(
                    client,
                    source,
                    config,
                    global_limit,
                    domain_limits,
                )
                for source in discovery_jobs
            )
        )
        for source, (feed_urls, status, error) in zip(
            discovery_jobs, discovered, strict=True
        ):
            source_feed_urls[source.id] = feed_urls
            discovery_statuses[source.id] = {
                "status": status,
                "error": error,
                "checked_at": checked_at,
                "feed_urls": feed_urls,
            }
            registry_sources[source.id] = {
                "source_url": source.url,
                "feed_urls": feed_urls,
                "checked_at": checked_at,
                "status": status,
                "error": error,
            }

        async def bounded_feed(
            source: SourceConfig, feed_url: str
        ) -> FeedFetchResult:
            """处理：在全局与同域配额内抓取单个 Feed。
            输入：
            - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级
              。
            - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
            输出：单个 Feed 的抓取结果；包含访问状态、缓存信息、错误、刷新时间和规范文章条目。
            """
            domain = urlsplit(feed_url).netloc.lower()
            async with global_limit, domain_limits[domain]:
                return await fetch_feed(
                    client,
                    source,
                    feed_url,
                    data_dir,
                    config.timezone,
                    max_bytes=config.monitor.max_feed_bytes,
                    max_items=(
                        source.feed_item_limit or config.monitor.max_items_per_feed
                    ),
                    refresh_interval_minutes=(
                        source.refresh_interval_minutes
                        or config.monitor.default_refresh_interval_minutes
                    ),
                    force=force,
                )

        jobs = [
            (source, feed_url)
            for source in sources
            for feed_url in source_feed_urls.get(source.id, [])
        ]
        results = await asyncio.gather(
            *(bounded_feed(source, feed_url) for source, feed_url in jobs)
        )
        initial_by_source: defaultdict[str, list[FeedFetchResult]] = defaultdict(list)
        for (source, _feed_url), result in zip(jobs, results, strict=True):
            initial_by_source[source.id].append(result)
        fallback_discovery_sources = [
            source
            for source in sources
            if config.monitor.auto_discover_feeds
            and source.feed_urls
            and not any(
                result.items for result in initial_by_source.get(source.id, [])
            )
        ]
        # 显式配置的 Feed 可能已迁移；只有全部未产出条目时才回退发现替代地址。
        fallback_discovered = await asyncio.gather(
            *(
                _discover_one(
                    client,
                    source,
                    config,
                    global_limit,
                    domain_limits,
                )
                for source in fallback_discovery_sources
            )
        )
        additional_jobs: list[tuple[SourceConfig, str]] = []
        for source, (discovered_urls, status, error) in zip(
            fallback_discovery_sources,
            fallback_discovered,
            strict=True,
        ):
            existing_urls = set(source_feed_urls.get(source.id, []))
            alternatives = [
                url for url in discovered_urls if url not in existing_urls
            ]
            additional_jobs.extend((source, url) for url in alternatives)
            discovery_statuses[source.id] = {
                "status": status,
                "error": error,
                "checked_at": checked_at,
                "feed_urls": discovered_urls,
                "fallback_after_configured_feed": True,
            }
            registry_sources[source.id] = {
                "source_url": source.url,
                "feed_urls": discovered_urls,
                "checked_at": checked_at,
                "status": status,
                "error": error,
            }
        if additional_jobs:
            additional_results = await asyncio.gather(
                *(
                    bounded_feed(source, feed_url)
                    for source, feed_url in additional_jobs
                )
            )
            jobs.extend(additional_jobs)
            results.extend(additional_results)
    by_source: defaultdict[str, list[FeedFetchResult]] = defaultdict(list)
    for (source, _feed_url), result in zip(jobs, results, strict=True):
        by_source[source.id].append(result)
    registry_payload = {
        "schema_version": "1.0",
        "updated_at": checked_at,
        "sources": registry_sources,
    }
    write_json(registry_path, registry_payload)
    return dict(by_source), discovery_statuses, registry_payload


def _source_status(
    feed_results: list[FeedFetchResult],
    html_results: list[SourceResult],
    discovery: dict[str, Any] | None,
    item_count: int,
) -> str:
    """处理：根据条目数量和 Feed、HTML、发现状态计算来源最终状态。
    输入：
    - ``feed_results``：同一来源各 Feed 的抓取结果；包含缓存、失败和规范条目。
    - ``html_results``：同一来源静态 HTML 或浏览器回退的采集结果。
    - ``discovery``：Feed 自动发现过程的状态、候选 URL 和错误记录。
    - ``item_count``：合并去重后的来源条目数；用于判定 empty、partial 或 success。
    输出：对应状态模型中的规范状态字符串，供恢复、健康统计或展示使用。
    """
    statuses = {
        str(result.status) for result in [*feed_results, *html_results]
    }
    if item_count and statuses <= {
        SourceStatus.SUCCESS,
        SourceStatus.NO_ITEMS,
    }:
        return SourceStatus.SUCCESS
    if item_count:
        # 已取得条目时保留失败信号为 partial，不能把部分成功伪装成完全成功。
        return SourceStatus.PARTIAL if any(
            status
            in {
                SourceStatus.PARTIAL,
                SourceStatus.FAILED,
                SourceStatus.RATE_LIMITED,
                SourceStatus.VERIFICATION_REQUIRED,
            }
            for status in statuses
        ) else SourceStatus.SUCCESS
    if SourceStatus.RATE_LIMITED in statuses:
        # 没有条目时优先保留访问失败语义，绝不能降级为 no_items。
        return SourceStatus.RATE_LIMITED
    if SourceStatus.VERIFICATION_REQUIRED in statuses:
        return SourceStatus.VERIFICATION_REQUIRED
    if SourceStatus.FAILED in statuses:
        return SourceStatus.FAILED
    if statuses:
        return SourceStatus.NO_ITEMS
    if discovery and discovery.get("status") in _MONITOR_SOURCE_STATUSES:
        return str(discovery["status"])
    return "unsupported"


def _merge_source_items(
    source: SourceConfig,
    feed_results: list[FeedFetchResult],
    html_results: list[SourceResult],
) -> list[dict[str, Any]]:
    """处理：按规范 URL 合并同一来源的 Feed 与 HTML 条目，并补齐来源策略元数据。
    输入：
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``feed_results``：同一来源各 Feed 的抓取结果；包含缓存、失败和规范条目。
    - ``html_results``：同一来源静态 HTML 或浏览器回退的采集结果。
    输出：可写入监控快照的规范文章记录；
      已按 canonical_url 去重、按发布时间稳定倒序、补齐来源策略与 source_rank，
      并受 source.max_items 限制。
    """
    by_canonical: dict[str, dict[str, Any]] = {}
    for result in feed_results:
        for article in result.items:
            by_canonical[article.canonical_url] = article.to_dict()
    for result in html_results:
        for article in result.items:
            payload = article.to_dict()
            metadata = payload.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata.update(
                    {
                        "role": source.role,
                        "tier": source.tier,
                        "bundle": source.bundle,
                        "language": source.language,
                        "region": source.region,
                        "acquisition_method": "html",
                    }
                )
            by_canonical.setdefault(article.canonical_url, payload)
    items = list(by_canonical.values())
    items.sort(
        key=lambda item: (
            str(item.get("published_at") or item.get("discovered_at") or ""),
            str(item.get("item_id") or ""),
        ),
        reverse=True,
    )
    for rank, item in enumerate(items[: source.max_items], start=1):
        metadata = item.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["source_rank"] = rank
    return items[: source.max_items]


def _source_record(
    source: SourceConfig,
    feed_results: list[FeedFetchResult],
    html_results: list[SourceResult],
    discovery: dict[str, Any] | None,
    items: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """处理：汇总来源状态、方法、错误和 Feed 结果形成健康记录。
    输入：
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``feed_results``：同一来源各 Feed 的抓取结果；包含缓存、失败和规范条目。
    - ``html_results``：同一来源静态 HTML 或浏览器回退的采集结果。
    - ``discovery``：Feed 自动发现过程的状态、候选 URL 和错误记录。
    - ``items``：规范条目列表；每项带稳定身份并可进入聚类、报告或渲染步骤。
    - ``generated_at``：当前快照或产物的生成时间；用于时效计算和确定性排序。
    输出：“汇总来源状态、方法、错误和 Feed 结果形成健康记录”形成的结构化字典；
      典型键包括 bundle、category、checked_at、error、feed_results、feed_urls、items_count、meth
      ods、module、region、role、source_id。
    """
    errors = [
        str(result.error)
        for result in [*feed_results, *html_results]
        if result.error
    ]
    status = _source_status(feed_results, html_results, discovery, len(items))
    methods = sorted(
        {
            str(item.get("metadata", {}).get("acquisition_method"))
            for item in items
            if isinstance(item.get("metadata"), dict)
            and item.get("metadata", {}).get("acquisition_method")
        }
    )
    return {
        "source_id": source.id,
        "source_name": source.name,
        "source_url": source.url,
        "status": status,
        "checked_at": generated_at,
        "role": source.role,
        "tier": source.tier,
        "bundle": source.bundle,
        "module": source.module,
        "category": source.category,
        "region": source.region,
        "methods": methods,
        "feed_urls": [result.feed_url for result in feed_results],
        "items_count": len(items),
        "stale": any(result.stale for result in feed_results),
        "error": "; ".join(dict.fromkeys(errors)) or (
            discovery.get("error") if discovery else None
        ),
        "feed_results": [
            {
                key: value
                for key, value in result.to_dict().items()
                if key != "items"
            }
            for result in feed_results
        ],
    }


def _merge_history_items(
    previous: dict[str, Any],
    new_items: list[dict[str, Any]],
    selected_source_ids: set[str],
    generated_at: str,
    config: AppConfig,
) -> list[dict[str, Any]]:
    """处理：以本轮条目优先合并仍在保留期内的历史条目，并清除已刷新来源的过期项。
    输入：
    - ``previous``：上一份监控快照对象；提供仍在保留期内的历史条目。
    - ``new_items``：本轮刷新产生的规范条目；优先于同 ID 的历史版本。
    - ``selected_source_ids``：本轮实际刷新的来源 ID；仅这些来源允许移除过期旧条目。
    - ``generated_at``：当前快照或产物的生成时间；用于时效计算和确定性排序。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    输出：“以本轮条目优先合并仍在保留期内的历史条目，
      并清除已刷新来源的过期项”得到的有序结构化记录；
      每项承载处理说明所定义的身份、证据或状态字段，可直接交给下一阶段。
    """
    generated = _parse_time(generated_at, config.timezone)
    assert generated is not None
    cutoff = generated - timedelta(hours=config.monitor.max_age_hours)
    merged: dict[str, dict[str, Any]] = {}
    for item in previous.get("items", []):
        if not isinstance(item, dict) or not item.get("item_id"):
            continue
        observed = _item_time(item, config.timezone)
        if observed is not None and observed < cutoff:
            continue
        payload = dict(item)
        if str(payload.get("source_id")) in selected_source_ids:
            # 本轮未再次观测到但仍在时效窗内的条目继续可见，并明确标记来源。
            metadata = payload.setdefault("metadata", {})
            if isinstance(metadata, dict):
                metadata["retained_from_previous_snapshot"] = True
        merged[str(payload["item_id"])] = payload
    for item in new_items:
        if not item.get("item_id"):
            continue
        observed = _item_time(item, config.timezone)
        if observed is not None and observed < cutoff:
            continue
        metadata = item.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata.pop("retained_from_previous_snapshot", None)
        merged[str(item["item_id"])] = item
    items = list(merged.values())
    items.sort(
        key=lambda item: (
            str(item.get("published_at") or item.get("discovered_at") or ""),
            str(item.get("item_id") or ""),
        ),
        reverse=True,
    )
    return items


def _update_health(
    data_dir: Path,
    source_records: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """处理：结合上一快照和本轮来源状态更新连续成功、失败及最近错误健康记录。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``source_records``：监控快照中的逐来源状态记录；用于更新连续成功/失败健康指标。
    - ``generated_at``：当前快照或产物的生成时间；用于时效计算和确定性排序。
    输出：“结合上一快照和本轮来源状态更新连续成功、失败及最近错误健康记录”形成的结构化字典；
      典型键包括 checks、consecutive_failures、error、last_checked_at、last_item_count、last_suc
      cess_at、methods、schema_version、source_id、source_name、sources、status。
    """
    path = data_dir / "monitor" / "health.json"
    previous = _safe_read_object(path)
    rows = {
        str(row.get("source_id")): row
        for row in previous.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }
    for source in source_records:
        source_id = str(source["source_id"])
        old = rows.get(source_id, {})
        checks = int(old.get("checks", 0)) + 1
        successful = source["status"] in {SourceStatus.SUCCESS, SourceStatus.PARTIAL}
        successes = int(old.get("successes", 0)) + int(successful)
        consecutive_failures = 0 if successful else int(old.get("consecutive_failures", 0)) + 1
        rows[source_id] = {
            "source_id": source_id,
            "source_name": source["source_name"],
            "status": source["status"],
            "checks": checks,
            "successes": successes,
            "success_rate": round(successes / checks, 4),
            "consecutive_failures": consecutive_failures,
            "last_checked_at": generated_at,
            "last_success_at": (
                generated_at if successful else old.get("last_success_at")
            ),
            "last_item_count": source["items_count"],
            "methods": source["methods"],
            "error": source["error"],
        }
    payload = {
        "schema_version": "1.0",
        "updated_at": generated_at,
        "sources": sorted(rows.values(), key=lambda row: str(row["source_id"])),
    }
    write_json(path, payload)
    return payload


def refresh_monitor(
    config: AppConfig,
    data_dir: Path,
    *,
    only_source_ids: set[str] | None = None,
    bundles: set[str] | None = None,
    include_discovery: bool = True,
    force: bool = False,
    html_fallback: bool | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    now: datetime | None = None,
) -> Path:
    """处理：刷新零模型本地新闻流、来源健康和事件聚类快照。
    输入：
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``only_source_ids``：调用方限定的来源 ID 集合；为空时处理配置选中的全部来源。
    - ``bundles``：要刷新的来源配置集合名称，例如 core 或 discovery。
    - ``include_discovery``：是否把独立 discovery 来源纳入本轮监控刷新。
    - ``force``：是否忽略正常缓存或重复保护并显式重新执行允许的步骤。
    - ``html_fallback``：Feed 无结果时是否允许对支持的来源执行静态 HTML 回退。
    - ``transport``：测试可注入的 HTTP 传输层；生产通常为空并使用真实网络。
    - ``now``：可注入的当前时间；测试可固定它，生产为空时读取配置时区时钟。
    输出：指向“刷新零模型本地新闻流、来源健康和事件聚类快照”所生成、定位或确认产物的本地路径。
    """
    if not config.monitor.enabled:
        raise RuntimeError("The local monitor is disabled in sources.yaml")
    available = (
        config.all_monitor_sources if include_discovery else list(config.sources)
    )
    sources = [
        source
        for source in available
        if source.enabled
        and (not only_source_ids or source.id in only_source_ids)
        and (not bundles or source.bundle in bundles)
    ]
    known_ids = {source.id for source in available}
    unknown = (only_source_ids or set()) - known_ids
    if unknown:
        raise ValueError(f"Unknown or disabled monitor sources: {sorted(unknown)}")
    if not sources:
        raise ValueError("No monitor sources matched the requested filters")

    current = _current_time(config.timezone, now)
    generated_at = current.isoformat(timespec="seconds")

    data_dir.mkdir(parents=True, exist_ok=True)
    previous = _safe_read_object(data_dir / "monitor" / "snapshot.json")
    feed_results, discovery_statuses, _registry = asyncio.run(
        _feed_phase(
            sources,
            config,
            data_dir,
            force=force,
            checked_at=generated_at,
            transport=transport,
        )
    )
    fallback_enabled = (
        config.monitor.html_fallback if html_fallback is None else html_fallback
    )
    fallback_sources = [
        source
        for source in sources
        if fallback_enabled
        and source.adapter_name == "browser_index"
        and not any(result.items for result in feed_results.get(source.id, []))
    ]
    # 浏览器只兜底支持该采集方式且 Feed 无结果的来源，避免默认扩大昂贵路径。
    prefetched = (
        prefetch_browser_pages(fallback_sources, config, data_dir)
        if fallback_sources and transport is None
        else {}
    )
    html_by_source: defaultdict[str, list[SourceResult]] = defaultdict(list)
    for (source_id, _url), result in prefetched.items():
        html_by_source[source_id].append(result)

    fresh_items: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    for source in sources:
        source_items = _merge_source_items(
            source,
            feed_results.get(source.id, []),
            html_by_source.get(source.id, []),
        )
        fresh_items.extend(source_items)
        source_records.append(
            _source_record(
                source,
                feed_results.get(source.id, []),
                html_by_source.get(source.id, []),
                discovery_statuses.get(source.id),
                source_items,
                generated_at,
            )
        )

    selected_ids = {source.id for source in sources}
    items = _merge_history_items(
        previous,
        fresh_items,
        selected_ids,
        generated_at,
        config,
    )
    previous_source_records = {
        str(row.get("source_id")): row
        for row in previous.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }
    previous_source_records.update(
        {str(row["source_id"]): row for row in source_records}
    )
    # 局部刷新只覆盖被选来源；未选择来源的上一轮健康记录继续保留。
    all_source_records = list(previous_source_records.values())
    health = _update_health(data_dir, source_records, generated_at)
    clusters = cluster_articles(
        items,
        generated_at,
        threshold=config.monitor.cluster_similarity_threshold,
        previous_clusters=[
            row for row in previous.get("clusters", []) if isinstance(row, dict)
        ],
    )
    status_counts: defaultdict[str, int] = defaultdict(int)
    for source in all_source_records:
        status_counts[str(source.get("status") or "unknown")] += 1
    pending = [
        {
            "source_id": source["source_id"],
            "source_name": source["source_name"],
            "status": source["status"],
            "url": source["source_url"],
            "error": source.get("error"),
        }
        for source in all_source_records
        if source.get("status")
        in {
            SourceStatus.FAILED,
            SourceStatus.RATE_LIMITED,
            SourceStatus.VERIFICATION_REQUIRED,
        }
    ]
    snapshot = {
        "schema_version": "2.0",
        "generated_at": generated_at,
        "timezone": config.timezone,
        "token_usage": 0,
        "collection_mode": "deterministic_feed_and_html",
        "summary": {
            "source_count": len(all_source_records),
            "item_count": len(items),
            "cluster_count": len(clusters),
            "multi_source_clusters": sum(
                int(cluster.get("source_count", 0)) > 1 for cluster in clusters
            ),
            "pending_source_count": len(pending),
            "status_breakdown": dict(sorted(status_counts.items())),
        },
        "sources": sorted(
            all_source_records,
            key=lambda row: (
                int(row.get("tier", 3)),
                str(row.get("source_name") or ""),
            ),
        ),
        "items": items,
        "clusters": clusters,
        "pending_verifications": pending,
        "health": health.get("sources", []),
    }
    output = data_dir / "monitor" / "snapshot.json"
    write_json(output, snapshot)
    return output


def fresh_monitor_snapshot_path(
    data_dir: Path,
    timezone: str,
    max_age_minutes: int,
    *,
    now: datetime | None = None,
) -> Path | None:
    """处理：返回近期有效的零 Token 快照，避免不必要的网络刷新。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``max_age_minutes``：监控快照允许复用的最大分钟年龄；超龄时返回无可用快照。
    - ``now``：可注入的当前时间；测试可固定它，生产为空时读取配置时区时钟。
    输出：指向“返回近期有效的零 Token 快照，
      避免不必要的网络刷新”所生成、定位或确认产物的本地路径；条件不满足时返回 None。
    """
    path = data_dir / "monitor" / "snapshot.json"
    snapshot = _safe_read_object(path)
    generated_at = _parse_time(snapshot.get("generated_at"), timezone)
    current = _current_time(timezone, now)
    if (
        generated_at is None
        or generated_at > current + timedelta(minutes=5)
        or current - generated_at > timedelta(minutes=max_age_minutes)
        or snapshot.get("token_usage") != 0
        or not isinstance(snapshot.get("sources"), list)
        or not isinstance(snapshot.get("items"), list)
    ):
        return None
    return path


def load_monitor_results(
    data_dir: Path,
    sources: list[SourceConfig],
    timezone: str,
    max_age_minutes: int,
    *,
    now: datetime | None = None,
) -> dict[str, SourceResult]:
    """处理：把有效监控快照转换为正式采集流程可复用的来源结果。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``sources``：本轮选择的来源配置列表；每项定义采集入口、策略和身份信息。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``max_age_minutes``：监控快照允许复用的最大分钟年龄；超龄时返回无可用快照。
    - ``now``：可注入的当前时间；测试可固定它，生产为空时读取配置时区时钟。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
    snapshot = _safe_read_object(data_dir / "monitor" / "snapshot.json")
    generated_at = _parse_time(snapshot.get("generated_at"), timezone)
    current = _current_time(timezone, now)
    if generated_at is None or current - generated_at > timedelta(minutes=max_age_minutes):
        return {}
    source_rows = {
        str(row.get("source_id")): row
        for row in snapshot.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }
    items_by_source: defaultdict[str, list[ArticleItem]] = defaultdict(list)
    allowed = {source.id for source in sources}
    for row in snapshot.get("items", []):
        if not isinstance(row, dict) or str(row.get("source_id")) not in allowed:
            continue
        with contextlib.suppress(TypeError, ValueError):
            items_by_source[str(row["source_id"])].append(article_from_dict(row))
    results: dict[str, SourceResult] = {}
    for source in sources:
        items = items_by_source.get(source.id, [])
        if not items:
            continue
        row = source_rows.get(source.id, {})
        results[source.id] = SourceResult(
            source_id=source.id,
            source_name=source.name,
            source_url=source.url,
            status=SourceStatus.SUCCESS,
            collected_at=str(snapshot["generated_at"]),
            module=source.module,
            category=source.category,
            page_title="Local monitor cache",
            final_url=source.url,
            error=row.get("error"),
            challenge={
                "monitor_cache": True,
                "snapshot_generated_at": snapshot["generated_at"],
            },
            items=items,
        )
    return results
