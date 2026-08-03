from __future__ import annotations

import hashlib
import math
import time
import xml.etree.ElementTree as ET
from contextlib import suppress
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .access import classify_access_text
from .adapters import is_eligible
from .config import SourceConfig
from .image_policy import normalize_image_candidates, srcset_candidates
from .models import ArticleItem, SourceStatus
from .utils import canonicalize_url, clean_title, item_id, read_json, write_json

_ARTICLE_FIELDS = {field.name for field in fields(ArticleItem)}
_FEED_ROOTS = {"feed", "rss", "rdf", "rdf:rdf"}
_IMAGE_EXTENSIONS = (".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp")


@dataclass(slots=True)
class FeedFetchResult:
    """处理：记录单个 Feed 的状态、缓存信息、错误和规范文章条目。
    输入：
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``source_name``：供读者展示的来源名称；与稳定 source_id 一起写入条目。
    - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
    - ``status``：当前操作或来源状态；值必须属于对应的显式状态模型。
    - ``checked_at``：本次来源或缓存检查时间；用于刷新计划和健康状态。
    - ``items``：规范条目列表；每项带稳定身份并可进入聚类、报告或渲染步骤。
    - ``http_status``：页面最近一次 HTTP 状态码；无网络响应时可为空。
    - ``error``：上游异常或错误信息；用于保留失败语义。
    - ``etag``：服务端 ETag；用于下一次条件请求避免重复下载。
    - ``last_modified``：服务端 Last-Modified 值；用于下一次条件请求。
    - ``cache_state``：本次 Feed 结果来自网络、命中缓存还是过期缓存。
    - ``stale``：结果是否来自超出正常刷新窗口但仍可用于降级的缓存。
    - ``latency_ms``：本次 Feed 请求或缓存读取耗时，单位为毫秒。
    - ``next_refresh_at``：按检查时间和间隔计算的下一次允许刷新时间。
    输出：构造后的 ``FeedFetchResult`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    source_id: str
    source_name: str
    feed_url: str
    status: str
    checked_at: str
    items: list[ArticleItem]
    http_status: int | None = None
    error: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    cache_state: str = "miss"
    stale: bool = False
    latency_ms: int = 0
    next_refresh_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """处理：将当前对象转换为可序列化字典。
        输入：
        - 无显式业务参数：不接收额外业务参数；
          从当前实例读取“将当前对象转换为可序列化字典”所需状态。
        输出：“将当前对象转换为可序列化字典”形成的结构化字典；
          键值表达该处理定义的业务记录或查找关系。
        """
        payload = asdict(self)
        payload["items"] = [item.to_dict() for item in self.items]
        payload["items_count"] = len(self.items)
        return payload


def _local_name(tag: str) -> str:
    """处理：去除 XML 命名空间并返回元素的本地标签名。
    输入：
    - ``tag``：XML 元素的完整标签名；函数会移除命名空间前缀。
    输出：“去除 XML 命名空间并返回元素的本地标签名”得到的规范字符串，供调用方存储、比较或展示。
    """
    return tag.rsplit("}", 1)[-1].split(":", 1)[-1].lower()


def _direct_elements(node: ET.Element, *names: str) -> list[ET.Element]:
    """处理：返回当前 XML 元素的指定直接子元素。
    输入：
    - ``node``：Feed 解析器当前处理的 RSS/Atom XML 元素。
    - ``*names``：允许匹配的直接子元素本地名称；按传入顺序查找。
    输出：按“返回当前 XML 元素的指定直接子元素”规则得到的 ``ET.Element`` 列表；
      列表顺序表达配置优先级、业务排名或稳定扫描顺序。
    """
    wanted = {name.lower() for name in names}
    return [child for child in list(node) if _local_name(child.tag) in wanted]


def _element_text(element: ET.Element | None) -> str:
    """处理：合并 XML 元素及其后代的可见文本。
    输入：
    - ``element``：可能为空的 RSS/Atom XML 元素；读取其自身及后代可见文本。
    输出：“合并 XML 元素及其后代的可见文本”得到的规范字符串，供调用方存储、比较或展示。
    """
    if element is None:
        return ""
    return clean_title(" ".join(element.itertext()))


def _direct_text(node: ET.Element, *names: str) -> str:
    """处理：读取指定直接子元素中的首个非空文本。
    输入：
    - ``node``：Feed 解析器当前处理的 RSS/Atom XML 元素。
    - ``*names``：允许匹配的直接子元素本地名称；按传入顺序查找。
    输出：“读取指定直接子元素中的首个非空文本”得到的规范字符串，供调用方存储、比较或展示。
    """
    for child in _direct_elements(node, *names):
        value = _element_text(child)
        if value:
            return value
    return ""


def _parse_datetime(value: str, timezone: str) -> datetime | None:
    """处理：把 Feed 日期文本解析并统一到配置时区。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：封装“把 Feed 日期文本解析并统一到配置时区”业务结果的 ``datetime | None`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    text = clean_title(value)
    if not text:
        return None
    parsed: datetime | None = None
    with suppress(TypeError, ValueError, OverflowError):
        parsed = parsedate_to_datetime(text)
    if parsed is None:
        candidate = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            for date_format in (
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M:%S",
            ):
                try:
                    parsed = datetime.strptime(text, date_format)
                    break
                except ValueError:
                    continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(ZoneInfo(timezone))


def _entry_link(node: ET.Element, feed_url: str) -> str:
    """处理：从 Atom/RSS 条目中选择规范文章链接。
    输入：
    - ``node``：Feed 解析器当前处理的 RSS/Atom XML 元素。
    - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
    输出：“从 Atom/RSS 条目中选择规范文章链接”得到的规范字符串，供调用方存储、比较或展示。
    """
    links = _direct_elements(node, "link")
    for link in links:
        href = clean_title(str(link.attrib.get("href") or ""))
        rel = clean_title(str(link.attrib.get("rel") or "alternate")).lower()
        if href and rel in {"", "alternate"}:
            return urljoin(feed_url, href)
    for link in links:
        value = _element_text(link)
        if value:
            return urljoin(feed_url, value)
    guid = _direct_text(node, "guid", "id")
    return guid if guid.startswith(("http://", "https://")) else ""


def _description(node: ET.Element) -> str:
    """处理：提取并清理 Feed 条目的摘要或正文片段。
    输入：
    - ``node``：Feed 解析器当前处理的 RSS/Atom XML 元素。
    输出：“提取并清理 Feed 条目的摘要或正文片段”得到的规范字符串，供调用方存储、比较或展示。
    """
    values: list[str] = []
    for child in list(node):
        if _local_name(child.tag) not in {
            "content",
            "description",
            "encoded",
            "summary",
        }:
            continue
        raw = "".join(child.itertext()).strip()
        if not raw:
            continue
        plain = (
            BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
            if "<" in raw
            else raw
        )
        values.append(clean_title(plain))
    return max(values, key=len, default="")[:600]


def _entry_image_candidates(
    node: ET.Element,
    description_html: str,
    base_url: str,
) -> list[str]:
    """处理：从 Feed 媒体字段和 HTML 摘要收集图片候选。
    输入：
    - ``node``：Feed 解析器当前处理的 RSS/Atom XML 元素。
    - ``description_html``：Feed 条目的摘要 HTML；仅解析图片标签，不执行脚本。
    - ``base_url``：解析相对链接时使用的最终页面或 Feed 基准 URL。
    输出：“从 Feed 媒体字段和 HTML 摘要收集图片候选”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    raw_candidates: list[str] = []
    for child in node.iter():
        name = _local_name(child.tag)
        url = str(child.attrib.get("url") or child.attrib.get("href") or "").strip()
        media_type = str(child.attrib.get("type") or "").lower()
        medium = str(child.attrib.get("medium") or "").lower()
        if not url:
            continue
        if (
            name in {"thumbnail", "image"}
            or (name in {"content", "enclosure"} and (
                media_type.startswith("image/")
                or medium == "image"
                or url.lower().split("?", 1)[0].endswith(_IMAGE_EXTENSIONS)
            ))
        ):
            raw_candidates.append(url)
    if "<img" in description_html.lower():
        for image in BeautifulSoup(description_html, "html.parser").find_all("img"):
            raw_candidates.extend(
                str(image.get(attribute) or "")
                for attribute in (
                    "src",
                    "data-src",
                    "data-original",
                    "data-lazy-src",
                )
            )
            raw_candidates.extend(srcset_candidates(image.get("srcset")))
    return normalize_image_candidates(raw_candidates, base_url)


def _provider(node: ET.Element) -> tuple[str | None, str | None]:
    """处理：根据 Feed 根元素识别 RSS 或 Atom 提供格式。
    输入：
    - ``node``：Feed 解析器当前处理的 RSS/Atom XML 元素。
    输出：“根据 Feed 根元素识别 RSS 或 Atom 提供格式”得到的固定结构结果；
      返回位置依次对应 None、None。
    """
    for child in _direct_elements(node, "source"):
        name = _direct_text(child, "title") or _element_text(child)
        url = str(child.attrib.get("url") or "").strip()
        if not url:
            link = next(iter(_direct_elements(child, "link")), None)
            url = str(link.attrib.get("href") or "") if link is not None else ""
        return name or None, url or None
    return None, None


def looks_like_feed(content: bytes | str, content_type: str = "") -> bool:
    """处理：结合响应类型和 XML 根标签判断内容是否为 RSS 或 Atom。
    输入：
    - ``content``：待编码、解析或写入的原始内容；边界和可信级别由当前函数说明。
    - ``content_type``：HTTP 内容类型或待上传文件 MIME 类型；用于解析、校验和响应头。
    输出：布尔判断；True 表示满足处理说明中的条件，False 表示不满足且不产生该结果。
    """
    if isinstance(content, bytes):
        prefix = content[:4096].decode("utf-8", errors="replace")
    else:
        prefix = content[:4096]
    normalized = prefix.lstrip("\ufeff \t\r\n").lower()
    if normalized.startswith(("<!doctype html", "<html")):
        return False
    if any(marker in normalized for marker in ("<rss", "<feed", "<rdf:rdf")):
        return True
    return any(
        marker in content_type.lower()
        for marker in ("application/rss+xml", "application/atom+xml")
    )


def parse_feed_document(
    content: bytes | str,
    source: SourceConfig,
    feed_url: str,
    collected_at: str,
    timezone: str,
    *,
    max_items: int,
) -> list[ArticleItem]:
    """处理：把有界且不可信的 RSS/Atom XML 解析为规范文章模型。
    输入：
    - ``content``：RSS/Atom HTTP 响应的原始 XML 字节或文本；按不可信输入和大小边界解析。
    - ``source``：当前 Feed 所属来源配置；提供来源身份、栏目、过滤规则和条目上限。
    - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
    - ``collected_at``：页面或 Feed 的采集时间；用于状态记录和时间回退，不冒充发布时间。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``max_items``：本步骤允许处理或返回的最大条目数；同时受全局预算限制。
    输出：可写入规范来源索引的文章条目列表；
      每项包含稳定 ID、来源身份、规范 URL、时间和可用元数据，
      并保持当前处理定义的筛选与排序语义。
    """
    raw = content.encode("utf-8") if isinstance(content, str) else content
    root = ET.fromstring(raw)
    if _local_name(root.tag) not in _FEED_ROOTS:
        raise ValueError(f"Unsupported feed root element: {_local_name(root.tag)!r}")
    entry_nodes = [
        node for node in root.iter() if _local_name(node.tag) in {"entry", "item"}
    ]
    observed = _parse_datetime(collected_at, timezone)
    if observed is None:
        observed = datetime.now(ZoneInfo(timezone))

    items: list[ArticleItem] = []
    seen: set[str] = set()
    for node in entry_nodes:
        title = _direct_text(node, "title")
        url = _entry_link(node, feed_url)
        if not is_eligible(source, title, url):
            continue
        published_text = _direct_text(
            node,
            "pubdate",
            "published",
            "issued",
            "date",
            "updated",
        )
        published = _parse_datetime(published_text, timezone)
        if published is not None and published > observed + timedelta(hours=1):
            continue
        canonical = canonicalize_url(url)
        if canonical in seen:
            continue
        description_nodes = _direct_elements(
            node, "content", "description", "encoded", "summary"
        )
        description_html = max(
            ("".join(child.itertext()).strip() for child in description_nodes),
            key=len,
            default="",
        )
        image_candidates = _entry_image_candidates(
            node,
            description_html,
            url or feed_url,
        )
        provider_name, provider_url = _provider(node)
        article = ArticleItem(
            item_id=item_id(source.id, canonical),
            source_id=source.id,
            source_name=source.name,
            title=title,
            url=url,
            canonical_url=canonical,
            discovered_at=collected_at,
            module=source.module,
            category=source.category,
            description=_description(node),
            published_at=(
                published.isoformat(timespec="seconds") if published is not None else None
            ),
            original_provider=provider_name,
            image_url=image_candidates[0] if image_candidates else None,
            metadata={
                "role": source.role,
                "tier": source.tier,
                "bundle": source.bundle,
                "language": source.language,
                "region": source.region,
                "acquisition_method": "feed",
                "feed_url": feed_url,
                "publication_time_missing": published is None,
                **({"original_provider_url": provider_url} if provider_url else {}),
                **(
                    {"image_candidates": image_candidates}
                    if len(image_candidates) > 1
                    else {}
                ),
            },
        )
        seen.add(canonical)
        items.append(article)
        if len(items) >= max_items:
            break
    return items


def discover_feed_urls(html: str, base_url: str) -> list[str]:
    """处理：在不执行页面脚本的前提下发现声明的 RSS/Atom 地址。
    输入：
    - ``html``：HTTP 或浏览器取得的不可信 HTML 文本；只执行静态解析。
    - ``base_url``：解析相对链接时使用的最终页面或 Feed 基准 URL。
    输出：“在不执行页面脚本的前提下发现声明的 RSS/Atom 地址”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []
    for link in soup.select("link[href], a[href]"):
        href = str(link.get("href") or "").strip()
        if not href:
            continue
        media_type = str(link.get("type") or "").lower()
        rel_value = link.get("rel") or []
        rel = " ".join(rel_value if isinstance(rel_value, list) else [str(rel_value)]).lower()
        resolved = urljoin(base_url, href)
        parsed = urlsplit(resolved)
        path = parsed.path.lower().rstrip("/")
        anchor_hint = any(
            marker in link.get_text(" ", strip=True).lower()
            for marker in ("rss", "atom", "feed")
        )
        declared = (
            "alternate" in rel
            and media_type in {"application/rss+xml", "application/atom+xml"}
        )
        endpoint_shape = (
            path.endswith(("/feed", "/rss", ".atom", ".rss", ".xml"))
            or "/rss/" in path
            or parsed.netloc.lower().startswith("rss.")
        )
        heuristic = endpoint_shape and (link.name == "link" or anchor_hint)
        if not (declared or heuristic):
            continue
        if resolved.startswith(("http://", "https://")):
            candidates.append(resolved)
    return list(dict.fromkeys(candidates))[:8]


def article_from_dict(payload: dict[str, Any]) -> ArticleItem:
    """处理：从缓存字典恢复包含来源身份、正文状态和元数据的文章对象。
    输入：
    - ``payload``：上游传入的结构化对象；函数只读取处理说明列出的受支持字段。
    输出：可进入规范来源索引的文章条目；包含稳定 ID、来源身份、规范 URL、时间和可用元数据。
    """
    return ArticleItem(**{key: value for key, value in payload.items() if key in _ARTICLE_FIELDS})


def _cache_path(data_dir: Path, feed_url: str) -> Path:
    """处理：根据 Feed URL 哈希生成隔离且稳定的缓存文件路径。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
    输出：指向“根据 Feed URL 哈希生成隔离且稳定的缓存文件路径”所生成、定位或确认产物的本地路径。
    """
    digest = hashlib.sha256(feed_url.encode("utf-8")).hexdigest()
    return data_dir / "monitor" / "feed-cache" / f"{digest}.json"


def _load_cache(data_dir: Path, feed_url: str) -> dict[str, Any]:
    """处理：读取单个 Feed 的本地缓存对象，文件缺失或损坏时返回空缓存。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
    输出：“读取单个 Feed 的本地缓存对象，文件缺失或损坏时返回空缓存”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    path = _cache_path(data_dir, feed_url)
    if not path.exists():
        return {}
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def _cached_items(cache: dict[str, Any]) -> list[ArticleItem]:
    """处理：从有效 Feed 缓存恢复文章条目列表。
    输入：
    - ``cache``：本地持久化缓存对象；包含状态、时间、响应元数据和可复用结果。
    输出：可写入规范来源索引的文章条目列表；
      每项包含稳定 ID、来源身份、规范 URL、时间和可用元数据，
      并保持当前处理定义的筛选与排序语义。
    """
    rows = cache.get("items", [])
    return [
        article_from_dict(row)
        for row in rows
        if isinstance(row, dict) and row.get("item_id")
    ]


def _retry_after_seconds(value: str | None) -> int | None:
    """处理：把 Retry-After 响应头解析为有界等待秒数。
    输入：
    - ``value``：待解析或规范化的单个输入值；非法值按函数契约返回空值或报错。
    输出：封装“把 Retry-After 响应头解析为有界等待秒数”业务结果的 ``int | None`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    if not value:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0, math.ceil((retry_at - datetime.now(UTC)).total_seconds()))


def _next_refresh(
    checked_at: datetime,
    interval_minutes: int,
    failures: int,
    retry_after: int | None = None,
) -> str:
    """处理：根据最近检查时间和刷新间隔计算下次刷新时间。
    输入：
    - ``checked_at``：本次来源或缓存检查时间；用于刷新计划和健康状态。
    - ``interval_minutes``：正常成功后到下一次允许刷新之间的分钟数。
    - ``failures``：该 Feed 连续失败次数；用于指数退避且设有 24 小时上限。
    - ``retry_after``：服务端 Retry-After 解析出的秒数；存在时优先于指数退避。
    输出：“根据最近检查时间和刷新间隔计算下次刷新时间”得到的规范字符串，
      供调用方存储、比较或展示。
    """
    if retry_after is not None:
        delay = max(60, retry_after)
    else:
        delay = interval_minutes * 60 * (2 ** min(failures, 5))
    delay = min(delay, 24 * 60 * 60)
    return (checked_at + timedelta(seconds=delay)).isoformat(timespec="seconds")


async def fetch_feed(
    client: httpx.AsyncClient,
    source: SourceConfig,
    feed_url: str,
    data_dir: Path,
    timezone: str,
    *,
    max_bytes: int,
    max_items: int,
    refresh_interval_minutes: int,
    force: bool = False,
) -> FeedFetchResult:
    """处理：使用条件请求、缓存和刷新策略抓取并解析单个 RSS/Atom Feed。
    输入：
    - ``client``：已配置超时、重定向和连接池策略的 HTTP 客户端。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``feed_url``：正在解析或抓取的 RSS/Atom 地址；同时作为缓存和来源追踪键。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``max_bytes``：允许读取或下载的最大字节数；达到上限后停止或报错。
    - ``max_items``：本步骤允许处理或返回的最大条目数；同时受全局预算限制。
    - ``refresh_interval_minutes``：来源或 Feed 的最小刷新间隔；未到期时可复用缓存。
    - ``force``：是否忽略正常缓存或重复保护并显式重新执行允许的步骤。
    输出：单个 Feed 的抓取结果；包含访问状态、缓存信息、错误、刷新时间和规范文章条目。
    """
    cache = _load_cache(data_dir, feed_url)
    checked_at = datetime.now(ZoneInfo(timezone))
    checked_iso = checked_at.isoformat(timespec="seconds")
    next_refresh_raw = str(cache.get("next_refresh_at") or "")
    if cache and not force and next_refresh_raw:
        next_refresh = _parse_datetime(next_refresh_raw, timezone)
        if next_refresh is not None and checked_at < next_refresh:
            cached = _cached_items(cache)
            return FeedFetchResult(
                source_id=source.id,
                source_name=source.name,
                feed_url=feed_url,
                status=(
                    SourceStatus.SUCCESS
                    if cached
                    else str(cache.get("status", SourceStatus.NO_ITEMS))
                ),
                checked_at=checked_iso,
                items=cached,
                http_status=cache.get("http_status"),
                error=cache.get("error"),
                etag=cache.get("etag"),
                last_modified=cache.get("last_modified"),
                cache_state="fresh",
                stale=False,
                next_refresh_at=next_refresh_raw,
            )

    headers = {
        "Accept": (
            "application/rss+xml,application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.1"
        ),
        "User-Agent": "DailyIntelligenceMonitor/2.0 (+local feed reader)",
    }
    if cache.get("etag"):
        headers["If-None-Match"] = str(cache["etag"])
    if cache.get("last_modified"):
        headers["If-Modified-Since"] = str(cache["last_modified"])
    started = time.perf_counter()
    response: httpx.Response | None = None
    try:
        async with client.stream("GET", feed_url, headers=headers) as response:
            status_code = response.status_code
            if status_code == 304:
                content = b""
            else:
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(
                            f"Feed exceeds configured {max_bytes}-byte safety limit"
                        )
                    chunks.append(chunk)
                content = b"".join(chunks)
    except Exception as exc:
        cached = _cached_items(cache)
        failures = int(cache.get("consecutive_failures", 0)) + 1
        next_refresh_at = _next_refresh(
            checked_at, refresh_interval_minutes, failures
        )
        error = f"{type(exc).__name__}: {exc}"
        write_json(
            _cache_path(data_dir, feed_url),
            {
                **cache,
                "feed_url": feed_url,
                "source_id": source.id,
                "status": SourceStatus.PARTIAL if cached else SourceStatus.FAILED,
                "error": error,
                "checked_at": checked_iso,
                "next_refresh_at": next_refresh_at,
                "consecutive_failures": failures,
            },
        )
        return FeedFetchResult(
            source_id=source.id,
            source_name=source.name,
            feed_url=feed_url,
            status=SourceStatus.PARTIAL if cached else SourceStatus.FAILED,
            checked_at=checked_iso,
            items=cached,
            error=error,
            etag=cache.get("etag"),
            last_modified=cache.get("last_modified"),
            cache_state="stale" if cached else "miss",
            stale=bool(cached),
            latency_ms=round((time.perf_counter() - started) * 1000),
            next_refresh_at=next_refresh_at,
        )

    assert response is not None
    latency_ms = round((time.perf_counter() - started) * 1000)
    status_code = response.status_code
    etag = response.headers.get("etag") or cache.get("etag")
    last_modified = response.headers.get("last-modified") or cache.get("last_modified")
    if status_code == 304:
        items = _cached_items(cache)
        next_refresh_at = _next_refresh(checked_at, refresh_interval_minutes, 0)
        write_json(
            _cache_path(data_dir, feed_url),
            {
                **cache,
                "status": SourceStatus.SUCCESS if items else SourceStatus.NO_ITEMS,
                "error": None,
                "http_status": status_code,
                "checked_at": checked_iso,
                "next_refresh_at": next_refresh_at,
                "consecutive_failures": 0,
            },
        )
        return FeedFetchResult(
            source_id=source.id,
            source_name=source.name,
            feed_url=feed_url,
            status=SourceStatus.SUCCESS if items else SourceStatus.NO_ITEMS,
            checked_at=checked_iso,
            items=items,
            http_status=status_code,
            etag=etag,
            last_modified=last_modified,
            cache_state="not_modified",
            latency_ms=latency_ms,
            next_refresh_at=next_refresh_at,
        )

    challenge = classify_access_text(
        status_code,
        "",
        content[:30000].decode("utf-8", errors="replace"),
    )
    retry_after = _retry_after_seconds(response.headers.get("retry-after"))
    if status_code == 429 or challenge.get("rate_limited"):
        status = SourceStatus.RATE_LIMITED
        error = f"HTTP {status_code}: feed rate limited"
        items = _cached_items(cache)
    elif status_code >= 400:
        status = (
            SourceStatus.VERIFICATION_REQUIRED
            if challenge.get("required")
            else SourceStatus.FAILED
        )
        error = f"HTTP {status_code}"
        items = _cached_items(cache)
    elif challenge.get("required") or not looks_like_feed(
        content, response.headers.get("content-type", "")
    ):
        status = (
            SourceStatus.VERIFICATION_REQUIRED
            if challenge.get("required")
            else SourceStatus.FAILED
        )
        error = "Response is HTML/challenge content, not RSS or Atom"
        items = _cached_items(cache)
    else:
        try:
            items = parse_feed_document(
                content,
                source,
                feed_url,
                checked_iso,
                timezone,
                max_items=max_items,
            )
        except (ET.ParseError, ValueError) as exc:
            status = SourceStatus.FAILED
            error = f"{type(exc).__name__}: {exc}"
            items = _cached_items(cache)
        else:
            status = SourceStatus.SUCCESS if items else SourceStatus.NO_ITEMS
            error = None

    failed = status in {
        SourceStatus.FAILED,
        SourceStatus.RATE_LIMITED,
        SourceStatus.VERIFICATION_REQUIRED,
    }
    stale = bool(items) and failed
    if stale:
        status = SourceStatus.PARTIAL
    failures = int(cache.get("consecutive_failures", 0)) + 1 if failed else 0
    next_refresh_at = _next_refresh(
        checked_at,
        refresh_interval_minutes,
        failures,
        retry_after if failed else None,
    )
    record = {
        "schema_version": "1.0",
        "feed_url": feed_url,
        "source_id": source.id,
        "status": status,
        "error": error,
        "http_status": status_code,
        "etag": etag,
        "last_modified": last_modified,
        "checked_at": checked_iso,
        "last_success_at": (
            checked_iso if not failed else cache.get("last_success_at")
        ),
        "next_refresh_at": next_refresh_at,
        "consecutive_failures": failures,
        "items": [item.to_dict() for item in items],
    }
    write_json(_cache_path(data_dir, feed_url), record)
    return FeedFetchResult(
        source_id=source.id,
        source_name=source.name,
        feed_url=feed_url,
        status=status,
        checked_at=checked_iso,
        items=items,
        http_status=status_code,
        error=error,
        etag=etag,
        last_modified=last_modified,
        cache_state="stale" if stale else "updated",
        stale=stale,
        latency_ms=latency_ms,
        next_refresh_at=next_refresh_at,
    )
