from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .access import CHALLENGE_TEXTS, classify_access_text
from .adapters import collect_candidates, is_eligible
from .config import (
    AppConfig,
    SourceConfig,
    resolve_browser_channel,
    resolve_profile_dir,
    source_urls,
)
from .models import SourceResult, SourceStatus
from .monitor import load_monitor_results
from .prefetch import page_needs_browser, prefetch_browser_pages
from .storage import next_revision, write_immutable_json
from .utils import now_iso, read_json, timestamp_slug, today_str, write_json

__all__ = ["CHALLENGE_TEXTS", "is_eligible"]

def detect_challenge(page: Page, http_status: int | None) -> dict[str, Any]:
    """处理：从页面标题、正文和 iframe 迹象识别登录、验证码或限流挑战。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    输出：“从页面标题、正文和 iframe 迹象识别登录、验证码或限流挑战”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    title = ""
    body = ""
    with contextlib.suppress(Exception):
        title = page.title().lower()
    with contextlib.suppress(Exception):
        body = page.locator("body").inner_text(timeout=3000).lower()[:30000]
    iframe_count = 0
    with contextlib.suppress(Exception):
        iframe_count = page.locator(
            'iframe[src*="captcha"], iframe[src*="challenge"], iframe[title*="challenge" i]'
        ).count()
    return classify_access_text(
        http_status,
        title,
        body,
        iframe_detected=iframe_count > 0,
    )


def classify_source_status(
    http_status: int | None,
    challenge_required: bool,
    has_items: bool,
    rate_limited: bool = False,
) -> SourceStatus:
    """处理：结合条目数量、访问挑战、限流和错误信息确定显式来源状态。
    输入：
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    - ``challenge_required``：是否检测到登录、验证码或其他人工验证挑战。
    - ``has_items``：本次来源采集是否产生至少一个规范条目。
    - ``rate_limited``：是否检测到服务端限流；为真时不得改写为正常无条目。
    输出：封装“结合条目数量、访问挑战、限流和错误信息确定显式来源状态”业务结果的 ``SourceStatus`
      ` 对象；调用方据此继续相邻阶段或识别无结果状态。
    """
    if rate_limited:
        return SourceStatus.RATE_LIMITED
    if challenge_required:
        return SourceStatus.VERIFICATION_REQUIRED
    if http_status is not None and http_status >= 400:
        return SourceStatus.FAILED
    return SourceStatus.SUCCESS if has_items else SourceStatus.NO_ITEMS


def collect_one(
    context: BrowserContext,
    source: SourceConfig,
    config: AppConfig,
    data_dir: Path,
) -> SourceResult:
    """处理：打开单个来源页面，等待加载后返回带状态和条目的采集结果。
    输入：
    - ``context``：浏览器、写作或报告上下文对象；包含当前阶段已经绑定的受控状态。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
    collected_at = now_iso(config.timezone)
    page = context.new_page()
    response = None
    try:
        response = page.goto(
            source.url,
            wait_until="domcontentloaded",
            timeout=config.browser.navigation_timeout_ms,
        )
        page.wait_for_timeout(source.wait_ms or config.browser.default_wait_ms)
        http_status = response.status if response else None
        return collect_loaded_page(page, source, config, http_status, collected_at)
    except Exception as exc:
        return SourceResult(
            source_id=source.id,
            source_name=source.name,
            source_url=source.url,
            status=SourceStatus.FAILED,
            collected_at=collected_at,
            module=source.module,
            category=source.category,
            page_title=_safe_title(page),
            final_url=page.url,
            http_status=response.status if response else None,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        page.close()


def collect_loaded_page(
    page: Page,
    source: SourceConfig,
    config: AppConfig,
    http_status: int | None = None,
    collected_at: str | None = None,
) -> SourceResult:
    """处理：检测已加载页面的访问挑战，并在可访问时提取候选文章。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    - ``collected_at``：页面或 Feed 的采集时间；用于状态记录和时间回退，不冒充发布时间。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
    collected_at = collected_at or now_iso(config.timezone)
    challenge = detect_challenge(page, http_status)
    page_title = _safe_title(page)
    # 挑战页上的文本和链接不是新闻条目；确认可访问前不得进入候选解析。
    items = [] if challenge["required"] else collect_candidates(page, source, collected_at)
    status = classify_source_status(
        http_status,
        challenge["required"],
        bool(items),
        bool(challenge.get("rate_limited")),
    )
    error = f"HTTP {http_status}" if http_status is not None and http_status >= 400 else None
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=status,
        collected_at=collected_at,
        module=source.module,
        category=source.category,
        page_title=page_title,
        final_url=page.url,
        http_status=http_status,
        error=error,
        challenge=challenge,
        items=items,
    )


def collect_source(
    context: BrowserContext | None,
    source: SourceConfig,
    config: AppConfig,
    data_dir: Path,
    prefetched_pages: dict[tuple[str, str], SourceResult] | None = None,
    monitor_result: SourceResult | None = None,
) -> SourceResult:
    """处理：采集单个来源的主页和扩展页，并合并预取、监控或浏览器结果。
    输入：
    - ``context``：浏览器、写作或报告上下文对象；包含当前阶段已经绑定的受控状态。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``prefetched_pages``：HTTP 预取阶段按来源与 URL 保存的页面结果；可避免再次导航。
    - ``monitor_result``：零模型监控器为同一来源生成的结果；采集阶段可复用其条目和状态。
    输出：供索引汇总的来源采集结果；包含明确状态、错误或挑战信息、页面元数据和规范文章条目。
    """
    prefetch_supplied = prefetched_pages is not None
    prefetched_pages = prefetched_pages or {}
    target = max(1, min(source.report_target, source.max_items))
    monitor_sufficient = bool(
        monitor_result and len(monitor_result.items) >= target
    )
    page_results: list[SourceResult] = [monitor_result] if monitor_result else []
    urls = [] if monitor_sufficient else source_urls(source, data_dir)
    for url in urls:
        prefetched = prefetched_pages.get((source.id, url))
        if not page_needs_browser(prefetched):
            page_results.append(prefetched)
            continue
        if context is None and prefetch_supplied:
            raise RuntimeError(
                f"Edge fallback required for source {source.id!r} page {url}, "
                "but no browser context is available"
            )
        page_results.append(
            collect_one(context, replace(source, url=url), config, data_dir)
        )
    for result in page_results:
        if result.status == SourceStatus.NO_ITEMS and (
            result.error or (result.http_status is not None and result.http_status >= 400)
        ):
            # 修复旧适配器可能产生的矛盾状态，访问失败不能伪装成正常无条目。
            result.status = SourceStatus.FAILED
    # 按页面轮询合并，避免第一个宽泛页面在达到上限前挤掉后续专题页面。
    seen: set[str] = set()
    items = []
    position = 0
    while len(items) < source.max_items:
        added = False
        for result in page_results:
            if position >= len(result.items):
                continue
            item = result.items[position]
            if item.canonical_url in seen:
                continue
            seen.add(item.canonical_url)
            items.append(item)
            added = True
            if len(items) >= source.max_items:
                break
        position += 1
        if not added and all(position >= len(result.items) for result in page_results):
            break
    for source_rank, item in enumerate(items, start=1):
        item.metadata["source_rank"] = source_rank
    statuses = {result.status for result in page_results}
    if items and statuses <= {SourceStatus.SUCCESS, SourceStatus.NO_ITEMS}:
        status = SourceStatus.SUCCESS
    elif items:
        status = SourceStatus.PARTIAL
    elif SourceStatus.RATE_LIMITED in statuses:
        status = SourceStatus.RATE_LIMITED
    elif SourceStatus.VERIFICATION_REQUIRED in statuses:
        status = SourceStatus.VERIFICATION_REQUIRED
    elif SourceStatus.FAILED in statuses:
        status = SourceStatus.FAILED
    else:
        status = SourceStatus.NO_ITEMS
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=status,
        collected_at=now_iso(config.timezone),
        module=source.module,
        category=source.category,
        error="; ".join(result.error for result in page_results if result.error) or None,
        page_results=[
            {
                "url": result.source_url,
                "final_url": result.final_url,
                "status": result.status,
                "http_status": result.http_status,
                "error": result.error,
                "challenge": result.challenge,
                "items_count": len(result.items),
                "acquisition_method": (
                    "monitor_cache"
                    if result.challenge.get("monitor_cache")
                    else "browser_or_http"
                ),
            }
            for result in page_results
        ],
        items=items,
    )


def _safe_title(page: Page) -> str:
    """处理：读取页面标题；浏览器访问失败时返回空字符串。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    输出：“读取页面标题；浏览器访问失败时返回空字符串”得到的规范字符串，
      供调用方存储、比较或展示。
    """
    try:
        return page.title()
    except Exception:
        return ""


def collect_sources(
    config: AppConfig,
    data_dir: Path,
    edition: str,
    headed: bool,
    only_source_ids: set[str] | None = None,
    profile_dir: Path | None = None,
    browser_channel: str | None = None,
    revision: int | None = None,
    temporary: bool = False,
) -> Path:
    """处理：按并发预算采集选定来源，并为每个来源保留成功、空、失败或挑战状态。
    输入：
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``edition``：日报版本标识，通常为 morning 或 evening；参与窗口和产物命名。
    - ``headed``：是否显示真实浏览器窗口；人工登录或验证场景需要开启。
    - ``only_source_ids``：调用方限定的来源 ID 集合；为空时处理配置选中的全部来源。
    - ``profile_dir``：持久化浏览器 Profile 目录；保存用户已授权的浏览器会话。
    - ``browser_channel``：Playwright 浏览器通道名称；为空时使用配置或默认 Chromium。
    - ``revision``：目标产物修订号；为空时由持久化层选择下一个不可变版本。
    - ``temporary``：是否生成重试或验证用的临时索引，而不是正式不可变索引。
    输出：指向“按并发预算采集选定来源，
      并为每个来源保留成功、空、失败或挑战状态”所生成、定位或确认产物的本地路径。
    """
    selected = [
        source
        for source in config.sources
        if source.enabled and (not only_source_ids or source.id in only_source_ids)
    ]
    unknown_ids = (only_source_ids or set()) - {source.id for source in selected}
    if unknown_ids:
        raise ValueError(f"Unknown or disabled sources: {sorted(unknown_ids)}")

    profile = resolve_profile_dir(config, profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    channel = resolve_browser_channel(config, browser_channel)
    monitor_results = load_monitor_results(
        data_dir,
        selected,
        config.timezone,
        config.monitor.snapshot_max_age_minutes,
    )
    html_sources = [
        source
        for source in selected
        if (
            len(monitor_results[source.id].items)
            if source.id in monitor_results
            else 0
        )
        < max(1, min(source.report_target, source.max_items))
    ]
    prefetched_pages = (
        {} if headed else prefetch_browser_pages(html_sources, config, data_dir)
    )
    requires_browser = any(
        page_needs_browser(prefetched_pages.get((source.id, url)))
        for source in html_sources
        for url in source_urls(source, data_dir)
    )
    if requires_browser:
        with sync_playwright() as playwright:
            kwargs: dict[str, Any] = {
                "user_data_dir": str(profile),
                "headless": not headed,
                "locale": "en-US",
                "timezone_id": config.timezone,
                "viewport": {"width": 1440, "height": 1000},
            }
            if channel:
                kwargs["channel"] = channel
            context = playwright.chromium.launch_persistent_context(**kwargs)
            try:
                results = [
                    collect_source(
                        context,
                        source,
                        config,
                        data_dir,
                        prefetched_pages,
                        monitor_results.get(source.id),
                    )
                    for source in selected
                ]
            finally:
                context.close()
    else:
        results = [
            collect_source(
                None,
                source,
                config,
                data_dir,
                prefetched_pages,
                monitor_results.get(source.id),
            )
            for source in selected
        ]

    return write_results_index(
        results,
        data_dir,
        edition,
        config.timezone,
        profile,
        source_policies={
            source.id: {
                "report_target": source.report_target,
                "report_max": source.report_max,
                "role": source.role,
                "tier": source.tier,
                "bundle": source.bundle,
            }
            for source in selected
        },
        revision=revision,
        temporary=temporary,
    )


def write_results_index(
    results: list[SourceResult],
    data_dir: Path,
    edition: str,
    timezone: str,
    profile: Path,
    source_policies: dict[str, dict[str, int]] | None = None,
    revision: int | None = None,
    temporary: bool = False,
) -> Path:
    """处理：把来源结果和根级条目写成新的不可变索引修订。
    输入：
    - ``results``：本轮各来源的采集结果；每项含状态、错误、页面证据和规范文章条目。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``edition``：日报版本标识，通常为 morning 或 evening；参与窗口和产物命名。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``profile``：已经解析并创建的浏览器 Profile 绝对路径。
    - ``source_policies``：按来源 ID 记录报告目标数和上限的策略映射。
    - ``revision``：目标产物修订号；为空时由持久化层选择下一个不可变版本。
    - ``temporary``：是否生成重试或验证用的临时索引，而不是正式不可变索引。
    输出：指向“把来源结果和根级条目写成新的不可变索引修订”所生成、定位或确认产物的本地路径。
    """
    date = today_str(timezone)
    generated_at = now_iso(timezone)
    index_dir = data_dir / "indexes" / date

    if temporary:
        resolved_revision = 0
        output = (
            data_dir
            / "runs"
            / "retries"
            / date
            / f"{edition}-{timestamp_slug(timezone)}.json"
        )
    else:
        resolved_revision = revision or next_revision(index_dir, edition)
        output = index_dir / f"{edition}-r{resolved_revision}.json"

    payload = {
        "schema_version": "1.1",
        "index_id": (
            f"retry-{date}-{edition}-{timestamp_slug(timezone)}"
            if temporary
            else f"index-{date}-{edition}-r{resolved_revision}"
        ),
        "date": date,
        "edition": edition,
        "revision": resolved_revision,
        "generated_at": generated_at,
        "timezone": timezone,
        "browser_profile": str(profile),
        "temporary": temporary,
        "source_policies": source_policies or {},
        "sources": [result.to_dict() for result in results],
        "items": [item.to_dict() for result in results for item in result.items],
    }
    # 正式索引与临时重试索引都不可覆盖；合并操作必须生成新的正式修订。
    write_immutable_json(output, payload)
    if not temporary:
        write_json(data_dir / "indexes" / "latest.json", payload)
    return output


def merge_resume_index(original_path: Path, retry_path: Path, data_dir: Path) -> Path:
    """处理：用重试来源替换原索引中的对应来源并生成新修订。
    输入：
    - ``original_path``：原始不可变来源索引路径；合并时保留其未重试来源和历史证据。
    - ``retry_path``：仅包含失败来源重试结果的索引路径；成功结果覆盖同来源旧状态。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“用重试来源替换原索引中的对应来源并生成新修订”所生成、定位或确认产物的本地路径。
    """
    original = read_json(original_path)
    retry = read_json(retry_path)
    if not isinstance(original, dict) or not isinstance(retry, dict):
        raise ValueError("Both original and retry indexes must be JSON objects")
    retried_ids = {row["source_id"] for row in retry.get("sources", [])}
    merged_sources = [
        row for row in original.get("sources", []) if row.get("source_id") not in retried_ids
    ] + retry.get("sources", [])
    merged_items = [
        item for item in original.get("items", []) if item.get("source_id") not in retried_ids
    ] + retry.get("items", [])

    date = str(original.get("date"))
    edition = str(original.get("edition"))
    index_dir = data_dir / "indexes" / date
    revision = next_revision(index_dir, edition)
    merged = dict(original)
    merged.update(
        {
            "schema_version": "1.1",
            "index_id": f"index-{date}-{edition}-r{revision}",
            "revision": revision,
            "generated_at": retry.get("generated_at"),
            "resumed_from": str(original_path.resolve()),
            "retry_artifact": str(retry_path.resolve()),
            "sources": merged_sources,
            "items": merged_items,
        }
    )
    output = index_dir / f"{edition}-r{revision}.json"
    write_immutable_json(output, merged)
    write_json(data_dir / "indexes" / "latest.json", merged)
    return output


def merge_verified_results(
    original_path: Path,
    captured: list[SourceResult],
    data_dir: Path,
) -> Path:
    """处理：把人工验证后采集的页面结果合并回原索引并生成新修订。
    输入：
    - ``original_path``：原始不可变来源索引路径；合并时保留其未重试来源和历史证据。
    - ``captured``：人工验证后重新采集成功的来源结果；用于替换待验证状态。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“把人工验证后采集的页面结果合并回原索引并生成新修订”所生成、定位或确认产物的本地路
      径。
    """
    original = read_json(original_path)
    if not isinstance(original, dict):
        raise ValueError("Original index must be a JSON object")
    captured_by_source: dict[str, list[SourceResult]] = {}
    for result in captured:
        captured_by_source.setdefault(result.source_id, []).append(result)

    merged_sources: list[dict[str, Any]] = []
    for row in original.get("sources", []):
        source_results = captured_by_source.get(row.get("source_id"), [])
        if not source_results:
            merged_sources.append(row)
            continue
        updated = dict(row)
        page_results = list(updated.get("page_results", []))
        nested_items = {
            item.get("item_id"): item
            for item in updated.get("items", [])
            if isinstance(item, dict) and item.get("item_id")
        }
        for result in source_results:
            page_results = [page for page in page_results if page.get("url") != result.source_url]
            page_results.append(
                {
                    "url": result.source_url,
                    "final_url": result.final_url,
                    "status": result.status,
                    "http_status": result.http_status,
                    "error": result.error,
                    "challenge": result.challenge,
                    "items_count": len(result.items),
                }
            )
            nested_items.update({item.item_id: item.to_dict() for item in result.items})
        updated["page_results"] = page_results
        updated["items"] = list(nested_items.values())
        updated["items_count"] = len(nested_items)
        for page in page_results:
            if page.get("status") == "no_items" and (
                page.get("error")
                or (
                    isinstance(page.get("http_status"), int)
                    and int(page["http_status"]) >= 400
                )
            ):
                page["status"] = "failed"
        updated["error"] = (
            "; ".join(str(page["error"]) for page in page_results if page.get("error"))
            or None
        )
        statuses = {str(page.get("status")) for page in page_results}
        if nested_items and statuses <= {"success", "no_items"}:
            updated["status"] = SourceStatus.SUCCESS
        elif nested_items:
            updated["status"] = SourceStatus.PARTIAL
        elif "rate_limited" in statuses:
            updated["status"] = SourceStatus.RATE_LIMITED
        elif "verification_required" in statuses:
            updated["status"] = SourceStatus.VERIFICATION_REQUIRED
        elif "failed" in statuses:
            updated["status"] = SourceStatus.FAILED
        else:
            updated["status"] = SourceStatus.NO_ITEMS
        merged_sources.append(updated)

    root_items = {
        item.get("item_id"): item
        for item in original.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    for results in captured_by_source.values():
        for result in results:
            root_items.update({item.item_id: item.to_dict() for item in result.items})

    date = str(original["date"])
    edition = str(original["edition"])
    revision = next_revision(data_dir / "indexes" / date, edition)
    merged = dict(original)
    merged.update(
        {
            "index_id": f"index-{date}-{edition}-r{revision}",
            "revision": revision,
            "generated_at": now_iso(str(original.get("timezone", "Asia/Shanghai"))),
            "verified_from": str(original_path.resolve()),
            "sources": merged_sources,
            "items": list(root_items.values()),
        }
    )
    output = data_dir / "indexes" / date / f"{edition}-r{revision}.json"
    write_immutable_json(output, merged)
    write_json(data_dir / "indexes" / "latest.json", merged)
    return output
