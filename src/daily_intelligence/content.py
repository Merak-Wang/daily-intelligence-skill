from __future__ import annotations

import asyncio
import contextlib
import html
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import BrowserContext, Page, async_playwright

from .access import classify_access_text
from .collector import CHALLENGE_TEXTS
from .config import AppConfig, SourceConfig, resolve_browser_channel, resolve_profile_dir
from .image_policy import normalize_image_candidates
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
    """处理：让旧版嵌套条目视图与规范根级条目数组保持一致。
    输入：
    - ``payload``：上游传入的结构化对象；函数只读取处理说明列出的受支持字段。
    输出：不返回新数据；完成“让旧版嵌套条目视图与规范根级条目数组保持一致”，
      副作用限于该处理声明的受控对象或产物。
    """
    # 根级 items 是规范记录；sources[].items[] 只是必须继续支持的旧版索引视图。
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
    """处理：按选择器读取首个非空页面元数据值。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``selectors``：按优先级排列的 CSS 选择器；用于寻找元数据或正文区域。
    输出：“按选择器读取首个非空页面元数据值”得到的规范字符串，供调用方存储、比较或展示。
    """
    for selector in selectors:
        locator = page.locator(selector)
        if await locator.count():
            value = await locator.first.get_attribute("content")
            value = value or await locator.first.get_attribute("datetime")
            if value:
                return value.strip()
    return ""


async def meta_contents(page: Page, selectors: list[str]) -> list[str]:
    """处理：按选择器收集并去重全部非空页面元数据值。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``selectors``：按优先级排列的 CSS 选择器；用于寻找元数据或正文区域。
    输出：“按选择器收集并去重全部非空页面元数据值”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    values: list[str] = []
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(min(await locator.count(), 8)):
            value = await locator.nth(index).get_attribute("content")
            value = value or await locator.nth(index).get_attribute("datetime")
            if value:
                values.append(value.strip())
    return values


async def extract_visible_text(page: Page, selectors: list[str]) -> tuple[str, str | None]:
    """处理：按正文选择器查找首个达到最小长度的可见文本区域。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``selectors``：按优先级排列的 CSS 选择器；用于寻找元数据或正文区域。
    输出：“按正文选择器查找首个达到最小长度的可见文本区域”得到的固定结构结果；
      返回位置依次对应 ''、None。
    """
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
    """处理：把规范条目元数据和提取正文写成带 YAML frontmatter 的 Markdown。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``body``：正文或 HTML 文本；保存前只作为数据处理。
    - ``retrieved_at``：正文成功提取或确认的 ISO 时间；写入 Markdown frontmatter。
    输出：不返回新数据；完成“把规范条目元数据和提取正文写成带 YAML frontmatter 的 Markdown”，
      副作用限于该处理声明的受控对象或产物。
    """
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
    """处理：从静态 HTML 中读取首个非空元数据值。
    输入：
    - ``soup``：由不可信 HTML 构建的 BeautifulSoup 文档；不会执行任何脚本。
    - ``selectors``：按优先级排列的 CSS 选择器；用于寻找元数据或正文区域。
    输出：“从静态 HTML 中读取首个非空元数据值”得到的规范字符串，供调用方存储、比较或展示。
    """
    for selector in selectors:
        node = soup.select_one(selector)
        if node is None:
            continue
        value = node.get("content") or node.get("datetime") or node.get_text(" ", strip=True)
        if value:
            return str(value).strip()
    return ""


def _html_meta_values(soup: BeautifulSoup, selectors: list[str]) -> list[str]:
    """处理：从静态 HTML 中收集全部匹配的元数据值。
    输入：
    - ``soup``：由不可信 HTML 构建的 BeautifulSoup 文档；不会执行任何脚本。
    - ``selectors``：按优先级排列的 CSS 选择器；用于寻找元数据或正文区域。
    输出：“从静态 HTML 中收集全部匹配的元数据值”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    values: list[str] = []
    for selector in selectors:
        for node in soup.select(selector):
            value = (
                node.get("content")
                or node.get("datetime")
                or node.get_text(" ", strip=True)
            )
            if value:
                values.append(str(value).strip())
    return values


def _apply_image_candidates(
    item: dict[str, Any],
    values: list[object],
    base_url: str,
) -> None:
    """处理：规范化图片候选，并同步主图片与候选元数据。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``values``：待规范化、匹配或渲染的一组输入值。
    - ``base_url``：解析相对链接时使用的最终页面或 Feed 基准 URL。
    输出：不返回新数据；完成“规范化图片候选，并同步主图片与候选元数据”，
      副作用限于该处理声明的受控对象或产物。
    """
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    stored_candidates = metadata.get("image_candidates")
    if not isinstance(stored_candidates, list):
        stored_candidates = []
    candidates = normalize_image_candidates(
        [*values, item.get("image_url"), *stored_candidates],
        base_url,
    )
    if not candidates:
        item.pop("image_url", None)
        metadata.pop("image_candidates", None)
        return
    item["image_url"] = candidates[0]
    if len(candidates) > 1:
        metadata["image_candidates"] = candidates
    else:
        metadata.pop("image_candidates", None)


def _static_visible_text(
    soup: BeautifulSoup,
    selectors: list[str],
) -> tuple[str, str | None]:
    """处理：移除噪声节点并选择信息量最大的静态正文区域。
    输入：
    - ``soup``：由不可信 HTML 构建的 BeautifulSoup 文档；不会执行任何脚本。
    - ``selectors``：按优先级排列的 CSS 选择器；用于寻找元数据或正文区域。
    输出：“移除噪声节点并选择信息量最大的静态正文区域”得到的固定结构结果；
      返回位置依次对应 best_text、best_selector。
    """
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
    """处理：按来源、条目和采集时间生成正文 Markdown 路径。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``item_id``：规范条目的稳定 ID；用于连接索引、正文、简报和图片。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：指向“按来源、条目和采集时间生成正文 Markdown 路径”所生成、定位或确认产物的本地路径。
    """
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
    """处理：应用惰性 HTTP 正文，并判断浏览器回退是否仍有价值。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``body_html``：HTTP 响应中提取的静态 HTML；作为不可信数据解析元信息和正文。
    - ``final_url``：导航或重定向完成后的页面 URL。
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
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
    image_candidates = _html_meta_values(
        soup,
        ['meta[property="og:image"]', 'meta[name="twitter:image"]'],
    )
    _apply_image_candidates(item, image_candidates, final_url)

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
    """处理：流式读取 HTTP 正文并在字节上限处停止。
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
    """处理：执行有界无脚本抓取，仅在 Edge 仍可能补充内容时返回真。
    输入：
    - ``client``：已配置超时、重定向和连接池策略的 HTTP 客户端。
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    source = config.source_by_id(str(item["source_id"]))
    metadata = item.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        item["metadata"] = metadata
    try:
        async with client.stream("GET", str(item["url"])) as response:
            # 外部响应始终只按不可信数据解析；不会执行页面脚本或其中的文字指令。
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
    """处理：按全局和同域并发限制批量执行无脚本正文提取。
    输入：
    - ``targets``：已按预算选中的规范条目记录；每项读取 item_id、URL、来源和正文状态。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``transport``：测试可注入的 HTTP 传输层；生产通常为空并使用真实网络。
    输出：“按全局和同域并发限制批量执行无脚本正文提取”得到的有序结构化记录；
      典型字段包括 Accept、User-Agent，可直接交给下一阶段。
    """
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
            """处理：在并发限制内执行单项任务。
            输入：
            - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
            输出：“在并发限制内执行单项任务”得到的固定结构结果；
              返回位置依次对应 item、await _extract_http_one(client, 。
            """
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
    """处理：从页面标题、正文和 iframe 迹象识别登录、验证码或限流挑战。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    输出：“从页面标题、正文和 iframe 迹象识别登录、验证码或限流挑战”形成的结构化字典；
      典型键包括 iframe_detected、matched_text、required。
    """
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
    """处理：保留调用方的重要性顺序，并忽略重复或未知条目 ID。
    输入：
    - ``items``：规范条目列表；每项带稳定身份并可进入聚类、报告或渲染步骤。
    - ``selected_ids``：调用方按重要性排序选中的条目 ID；正文阶段只处理这些授权条目。
    - ``max_items``：本步骤允许处理或返回的最大条目数；同时受全局预算限制。
    输出：“保留调用方的重要性顺序，并忽略重复或未知条目 ID”得到的有序结构化记录；
      每项承载处理说明所定义的身份、证据或状态字段，可直接交给下一阶段。
    """
    by_id = {
        str(item.get("item_id")): item
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }
    ordered_ids = dict.fromkeys(selected_ids)
    return [by_id[item_id] for item_id in ordered_ids if item_id in by_id][:max_items]


def _domain_key(url: str) -> str:
    """处理：从 URL 提取用于同域并发限制的规范主机键。
    输入：
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：可跨修订关联的稳定字符串标识，供索引、状态或发布记录使用。
    """
    return urlsplit(url).netloc.lower().removeprefix("www.") or "unknown"


async def _extract_one(
    context: BrowserContext,
    item: dict[str, Any],
    config: AppConfig,
    data_dir: Path,
) -> None:
    """处理：在浏览器中提取单篇文章的元数据、正文和访问状态。
    输入：
    - ``context``：浏览器、写作或报告上下文对象；包含当前阶段已经绑定的受控状态。
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：不返回新数据；完成“在浏览器中提取单篇文章的元数据、正文和访问状态”，
      副作用限于该处理声明的受控对象或产物。
    """
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
        image_candidates = await meta_contents(
            page,
            ['meta[property="og:image"]', 'meta[name="twitter:image"]'],
        )
        _apply_image_candidates(item, image_candidates, page.url)
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
    """处理：执行有界正文提取，跨域并行且同域保持礼貌限流。
    输入：
    - ``context``：浏览器、写作或报告上下文对象；包含当前阶段已经绑定的受控状态。
    - ``targets``：已按预算选中的规范条目记录；每项读取 item_id、URL、来源和正文状态。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：不返回新数据；完成“执行有界正文提取，跨域并行且同域保持礼貌限流”，
      副作用限于该处理声明的受控对象或产物。
    """
    global_limit = max(1, config.browser.global_concurrency)
    domain_limit = max(1, config.browser.per_domain_concurrency)
    global_semaphore = asyncio.Semaphore(global_limit)
    domain_semaphores: dict[str, asyncio.Semaphore] = {}

    async def guarded(item: dict[str, Any]) -> None:
        """处理：在并发限制内执行单项任务。
        输入：
        - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
        输出：不返回新数据；完成“在并发限制内执行单项任务”，
          副作用限于该处理声明的受控对象或产物。
        """
        domain = _domain_key(str(item.get("url", "")))
        domain_semaphore = domain_semaphores.setdefault(
            domain, asyncio.Semaphore(domain_limit)
        )
        # 先取得同域名配额，避免同域等待者占满全局配额并阻塞无关来源。
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
    """处理：启动持久化浏览器上下文并批量处理 HTTP 回退条目。
    输入：
    - ``targets``：已按预算选中的规范条目记录；每项读取 item_id、URL、来源和正文状态。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``headed``：是否显示真实浏览器窗口；人工登录或验证场景需要开启。
    - ``profile``：已经解析并创建的浏览器 Profile 绝对路径。
    - ``channel``：Playwright 浏览器通道；为空时使用配置解析出的默认浏览器。
    输出：不返回新数据；完成“启动持久化浏览器上下文并批量处理 HTTP 回退条目”，
      副作用限于该处理声明的受控对象或产物。
    """
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
    """处理：验证条目正文状态和数据根内文件，判断能否安全复用。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
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
        # 即使索引被篡改，也不允许把数据根之外的任意文件当作已缓存正文。
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
    """处理：复用有效正文，先执行 HTTP 提取，再对必要条目进行浏览器回退。
    输入：
    - ``targets``：已按预算选中的规范条目记录；每项读取 item_id、URL、来源和正文状态。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``headed``：是否显示真实浏览器窗口；人工登录或验证场景需要开启。
    - ``profile``：已经解析并创建的浏览器 Profile 绝对路径。
    - ``channel``：Playwright 浏览器通道；为空时使用配置解析出的默认浏览器。
    输出：“复用有效正文，先执行 HTTP 提取，再对必要条目进行浏览器回退”形成的结构化字典；
      典型键包括 browser_fallback、browser_seconds、cache_hits、http_attempted、http_seconds、ht
      tp_successful、selected、successful、total_seconds。
    """
    started = time.perf_counter()
    reusable = [item for item in targets if _has_reusable_content(item, data_dir)]
    for item in reusable:
        metadata = item.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["content_acquisition"] = "cache"
    reusable_ids = {id(item) for item in reusable}
    pending = [item for item in targets if id(item) not in reusable_ids]

    http_started = time.perf_counter()
    # 先走无脚本 HTTP 快路径；只有失败或内容不足的条目才进入真实浏览器。
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
    """处理：按选中条目和全文预算复用已有正文，再执行 HTTP 与浏览器分层提取。
    输入：
    - ``index_path``：版本化来源索引 JSON 路径；包含根级规范 items 和来源采集状态。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``selected_ids``：调用方按重要性排序选中的条目 ID；正文阶段只处理这些授权条目。
    - ``max_items``：本步骤允许处理或返回的最大条目数；同时受全局预算限制。
    - ``headed``：是否显示真实浏览器窗口；人工登录或验证场景需要开启。
    - ``profile_dir``：持久化浏览器 Profile 目录；保存用户已授权的浏览器会话。
    - ``browser_channel``：Playwright 浏览器通道名称；为空时使用配置或默认 Chromium。
    输出：指向“按选中条目和全文预算复用已有正文，
      再执行 HTTP 与浏览器分层提取”所生成、定位或确认产物的本地路径。
    """
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
    # 修订产物不可覆盖；latest.json 只是可重建的便利指针。
    write_immutable_json(output, payload)
    write_json(data_dir / "indexes" / "latest.json", payload)
    return output
