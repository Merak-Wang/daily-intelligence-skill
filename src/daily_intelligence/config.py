from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .localization import validate_output_language
from .models import ITEM_ORDER_VALUES
from .taxonomy import validate_content_taxonomy
from .utils import environment_value, now_iso, read_json, write_json

SOURCE_PAGE_REWRITES = {
    ("huggingface_papers", "https://huggingface.co/papers/month"): (
        "https://huggingface.co/papers/trending"
    ),
    ("huggingface_papers", "https://huggingface.co/papers"): (
        "https://huggingface.co/papers/trending"
    ),
}

@dataclass(slots=True)
class SourceConfig:
    """处理：保存来源配置及其默认值。
    输入：
    - ``id``：对象的稳定配置 ID；写入后用于跨配置、索引和状态记录关联。
    - ``name``：待读取、注册或解析的稳定名称。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    - ``mode``：来源采集模式；决定使用浏览器索引还是专用适配器等入口。
    - ``adapter``：可选专用采集适配器名称或函数；未设置时使用来源 mode。
    - ``enabled``：是否启用当前来源或子系统；关闭后不会进入正常运行路径。
    - ``role``：来源在报告中的证据角色，例如 primary、evidence 或 discovery。
    - ``tier``：来源可信层级 1 至 3；影响代表条目选择和重要性评分。
    - ``bundle``：来源所属配置集合，例如 core 或 discovery；用于运行时筛选。
    - ``module``：报告顶层领域 ID，例如 information 或 technology。
    - ``category``：报告栏目 ID；必须与 module 和当前 taxonomy 契约一致。
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    - ``region``：来源主要覆盖地区；用于元数据和后续筛选。
    - ``feed_urls``：显式配置的 RSS/Atom 地址，按优先级尝试并与发现缓存合并。
    - ``monitor_enabled``：是否允许零模型监控器刷新该来源。
    - ``refresh_interval_minutes``：来源或 Feed 的最小刷新间隔；未到期时可复用缓存。
    - ``feed_item_limit``：单个来源 Feed 最多保留的条目数；为空时使用监控默认值。
    - ``include_domains``：候选文章允许出现的主机名白名单；空列表表示不额外限制。
    - ``article_patterns``：文章 URL 必须匹配的正则表达式；用于排除栏目页和非文章链接。
    - ``exclude_patterns``：候选 URL 命中后必须排除的正则表达式。
    - ``exclude_title_patterns``：候选标题命中后必须排除的正则表达式。
    - ``explore_urls``：除来源主页外需要采集的专题或栏目页 URL。
    - ``content_selectors``：按优先级寻找文章正文的 CSS 选择器。
    - ``max_items``：本步骤允许处理或返回的最大条目数；同时受全局预算限制。
    - ``report_target``：正常报告希望从该来源选入的条目数。
    - ``report_max``：该来源在单份报告中允许出现的最大条目数。
    - ``wait_ms``：页面初次加载后等待动态内容稳定的毫秒数；为空时使用全局默认值。
    - ``item_order``：可选来源级排序覆盖；为空时继承全局采集配置。
    输出：构造后的 ``SourceConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    id: str
    name: str
    url: str
    mode: str = "browser_index"
    adapter: str | None = None
    enabled: bool = True
    role: str = "evidence"
    tier: int = 2
    bundle: str = "core"
    module: str = "information"
    category: str = "international"
    language: str = "en"
    region: str = "global"
    feed_urls: list[str] = field(default_factory=list)
    monitor_enabled: bool = True
    refresh_interval_minutes: int | None = None
    feed_item_limit: int | None = None
    include_domains: list[str] = field(default_factory=list)
    article_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)
    exclude_title_patterns: list[str] = field(default_factory=list)
    explore_urls: list[str] = field(default_factory=list)
    content_selectors: list[str] = field(default_factory=lambda: ["article", "main"])
    max_items: int = 60
    report_target: int = 15
    report_max: int = 15
    wait_ms: int | None = None
    item_order: str | None = None

    @property
    def adapter_name(self) -> str:
        """处理：返回显式适配器名称，未配置时使用来源模式。
        输入：
        - 无显式业务参数：不接收额外业务参数；从当前实例读取“返回显式适配器名称，
          未配置时使用来源模式”所需状态；实现会明确读取属性 adapter、mode。
        输出：“返回显式适配器名称，未配置时使用来源模式”得到的规范字符串，
          供调用方存储、比较或展示。
        """
        return self.adapter or self.mode


@dataclass(slots=True)
class BrowserConfig:
    """处理：保存浏览器配置及其默认值。
    输入：
    - ``profile_dir_env``：保存浏览器 Profile 路径的环境变量名称。
    - ``channel_env``：保存 Playwright 浏览器通道的环境变量名称。
    - ``default_channel``：未显式配置时使用的 Playwright 浏览器通道。
    - ``global_concurrency``：整个阶段允许同时执行的最大任务数。
    - ``per_domain_concurrency``：同一域名允许同时执行的最大请求数，避免对单站点施压。
    - ``collection_global_concurrency``：无脚本正文 HTTP 提取的全局并发上限。
    - ``collection_per_domain_concurrency``：无脚本正文 HTTP 提取的同域并发上限。
    - ``http_prefetch_timeout_ms``：无脚本 HTTP 预取单次等待上限，单位为毫秒。
    - ``navigation_timeout_ms``：浏览器页面导航等待上限，单位为毫秒。
    - ``default_wait_ms``：来源未单独配置时的页面稳定等待时间，单位为毫秒。
    输出：构造后的 ``BrowserConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    profile_dir_env: str = "DAILY_INTEL_PROFILE_DIR"
    channel_env: str = "DAILY_INTEL_BROWSER_CHANNEL"
    default_channel: str = ""
    global_concurrency: int = 3
    per_domain_concurrency: int = 1
    collection_global_concurrency: int = 8
    collection_per_domain_concurrency: int = 2
    http_prefetch_timeout_ms: int = 15000
    navigation_timeout_ms: int = 45000
    default_wait_ms: int = 3500


@dataclass(slots=True)
class CollectionConfig:
    """处理：保存全来源统一的候选排序默认值。
    输入：
    - ``item_order``：来源未单独覆盖时的候选顺序；``source`` 保留网页或 Feed 的原始
      Top 顺序，``published_at`` 按可用发布时间倒序。
    输出：构造后的采集排序配置；AppConfig 会把该默认值补到所有正式与发现来源。
    """

    item_order: str = "source"


@dataclass(slots=True)
class BudgetConfig:
    """处理：保存预算配置及其默认值。
    输入：
    - ``max_runtime_seconds``：完整运行允许消耗的总秒数；用于截止时间和降级判断。
    - ``max_agent_tokens``：一次运行允许交给模型阶段的最大 Token 预算。
    - ``context_items_per_source``：情境包为每个来源最多保留的候选条目数。
    - ``report_items_per_source``：报告计划为每个来源设置的默认目标条目数。
    - ``max_fulltext_per_run``：单次运行允许提取全文的最大条目数。
    输出：构造后的 ``BudgetConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    max_runtime_seconds: int = 3600
    max_agent_tokens: int = 10_000_000
    context_items_per_source: int = 25
    report_items_per_source: int = 15
    max_fulltext_per_run: int = 12


@dataclass(slots=True)
class OutputConfig:
    """处理：保存输出配置及其默认值。
    输入：
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    - ``formats``：需要生成的本地投影格式列表，目前支持 html 和 pdf。
    - ``pdf_engine``：PDF 渲染引擎选择；决定使用 Edge、ReportLab 或自动回退。
    - ``open_after_finalize``：完成本地输出后是否用默认浏览器打开 HTML。
    - ``copy_html_to_desktop``：是否额外生成可独立打开的桌面 HTML 副本。
    - ``desktop_dir``：桌面副本目标目录；为空时按当前操作系统推断。
    输出：构造后的 ``OutputConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    language: str = "zh-CN"
    formats: list[str] = field(default_factory=lambda: ["html", "pdf"])
    pdf_engine: str = "edge"
    open_after_finalize: bool = False
    copy_html_to_desktop: bool = False
    desktop_dir: str | None = None


@dataclass(slots=True)
class MediaConfig:
    """处理：保存媒体配置及其默认值。
    输入：
    - ``enabled``：是否启用当前来源或子系统；关闭后不会进入正常运行路径。
    - ``max_images_per_report``：单份报告允许成功附加的图片数量上限。
    - ``max_image_bytes``：单张图片允许下载和保存的最大字节数。
    - ``max_total_bytes``：单份报告全部唯一图片允许占用的总字节预算。
    - ``max_image_pixels``：图片解码后的最大总像素数。
    - ``request_timeout_seconds``：一次 HTTP 请求或整段下载允许等待的秒数。
    - ``max_redirects``：图片下载允许跟随的最大重定向次数；每跳都会重新校验。
    - ``global_concurrency``：整个阶段允许同时执行的最大任务数。
    - ``per_domain_concurrency``：同一域名允许同时执行的最大请求数，避免对单站点施压。
    - ``cache_success_ttl_hours``：成功图片缓存无需重新下载的有效小时数。
    - ``cache_failure_retry_minutes``：失败图片再次允许尝试前的负缓存分钟数。
    输出：构造后的 ``MediaConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    enabled: bool = True
    max_images_per_report: int = 1000
    max_image_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 80 * 1024 * 1024
    max_image_pixels: int = 25_000_000
    request_timeout_seconds: float = 10.0
    max_redirects: int = 5
    global_concurrency: int = 12
    per_domain_concurrency: int = 2
    cache_success_ttl_hours: int = 168
    cache_failure_retry_minutes: int = 60


@dataclass(slots=True)
class MonitorConfig:
    """处理：保存监控配置及其默认值。
    输入：
    - ``enabled``：是否启用当前来源或子系统；关闭后不会进入正常运行路径。
    - ``sources_file``：可选外部来源配置文件名；为空时使用默认 sources.yaml。
    - ``refresh_before_edition``：日报准备阶段是否强制刷新监控数据。
    - ``reuse_fresh_snapshot_before_edition``：正式日报开始前是否优先复用仍新鲜的监控快照。
    - ``auto_discover_feeds``：来源未配置可用 Feed 时是否自动访问首页发现 RSS/Atom。
    - ``html_fallback``：Feed 无结果时是否允许对支持的来源执行静态 HTML 回退。
    - ``request_timeout_seconds``：一次 HTTP 请求或整段下载允许等待的秒数。
    - ``global_concurrency``：整个阶段允许同时执行的最大任务数。
    - ``per_domain_concurrency``：同一域名允许同时执行的最大请求数，避免对单站点施压。
    - ``max_feed_bytes``：单个 Feed 响应允许读取的最大字节数。
    - ``max_items_per_feed``：单个 Feed 解析后最多保留的文章条目数。
    - ``max_age_hours``：监控快照中条目允许保留的最大小时数。
    - ``snapshot_max_age_minutes``：正式日报可直接复用监控快照的最大分钟年龄。
    - ``default_refresh_interval_minutes``：来源未配置时使用的 Feed 默认刷新间隔。
    - ``cluster_similarity_threshold``：标题词法向量达到后可合并为同一故事的阈值。
    输出：构造后的 ``MonitorConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    enabled: bool = True
    sources_file: str | None = "discovery-sources.yaml"
    refresh_before_edition: bool = True
    reuse_fresh_snapshot_before_edition: bool = True
    auto_discover_feeds: bool = True
    html_fallback: bool = True
    request_timeout_seconds: float = 10.0
    global_concurrency: int = 16
    per_domain_concurrency: int = 2
    max_feed_bytes: int = 2 * 1024 * 1024
    max_items_per_feed: int = 40
    max_age_hours: int = 168
    snapshot_max_age_minutes: int = 90
    default_refresh_interval_minutes: int = 30
    cluster_similarity_threshold: float = 0.68


def validate_output_config(output: OutputConfig) -> OutputConfig:
    """处理：校验输出配置并在不满足约束时报告错误。
    输入：
    - ``output``：本地 HTML、PDF、桌面复制和打开行为配置。
    输出：封装“校验输出配置并在不满足约束时报告错误”业务结果的 ``OutputConfig`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    output.language = validate_output_language(output.language)
    allowed_formats = {"html", "pdf"}
    unknown_formats = set(output.formats) - allowed_formats
    if unknown_formats:
        raise ValueError(
            "Unsupported local output formats: " + ", ".join(sorted(unknown_formats))
        )
    output.formats = list(dict.fromkeys(output.formats))
    if "pdf" in output.formats and "html" not in output.formats:
        raise ValueError("PDF output requires HTML because PDF is rendered from the local HTML")
    if output.pdf_engine not in {"edge", "reportlab", "auto"}:
        raise ValueError("output.pdf_engine must be one of: edge, reportlab, auto")
    if output.desktop_dir and not Path(output.desktop_dir).expanduser().is_absolute():
        raise ValueError("output.desktop_dir must be an absolute path")
    return output


def validate_media_config(media: MediaConfig) -> MediaConfig:
    """处理：校验媒体配置并在不满足约束时报告错误。
    输入：
    - ``media``：报告图片下载、缓存、安全和总量预算配置。
    输出：封装“校验媒体配置并在不满足约束时报告错误”业务结果的 ``MediaConfig`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    if media.max_images_per_report < 0:
        raise ValueError("media.max_images_per_report must be non-negative")
    if not 0 < media.max_image_bytes <= 20 * 1024 * 1024:
        raise ValueError(
            "media.max_image_bytes must be positive and no more than Notion's "
            "20 MB direct-upload limit"
        )
    if media.max_total_bytes < media.max_image_bytes:
        raise ValueError("media.max_total_bytes must be at least media.max_image_bytes")
    if media.max_image_pixels <= 0:
        raise ValueError("media.max_image_pixels must be positive")
    if media.request_timeout_seconds <= 0:
        raise ValueError("media.request_timeout_seconds must be positive")
    if not 0 <= media.max_redirects <= 10:
        raise ValueError("media.max_redirects must be between 0 and 10")
    if media.global_concurrency <= 0 or media.per_domain_concurrency <= 0:
        raise ValueError("media concurrency values must be positive")
    if media.per_domain_concurrency > media.global_concurrency:
        raise ValueError(
            "media.per_domain_concurrency must not exceed global_concurrency"
        )
    if media.cache_success_ttl_hours <= 0:
        raise ValueError("media.cache_success_ttl_hours must be positive")
    if media.cache_failure_retry_minutes <= 0:
        raise ValueError("media.cache_failure_retry_minutes must be positive")
    return media


@dataclass(slots=True)
class AppConfig:
    """处理：保存应用配置及其默认值。
    输入：
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    - ``browser``：浏览器与 HTTP 正文提取配置。
    - ``sources``：本轮选择的来源配置列表；每项定义采集入口、策略和身份信息。
    - ``collection``：所有正式与发现来源共用的默认候选排序策略。
    - ``budget``：运行时长、模型 Token、情境条目和全文提取预算。
    - ``output``：本地 HTML、PDF、桌面复制和打开行为配置。
    - ``media``：报告图片下载、缓存、安全和总量预算配置。
    - ``monitor``：零模型监控的刷新、并发、缓存和聚类配置。
    - ``monitor_sources``：发现来源配置列表；与核心来源分开以避免改变报告配额。
    输出：构造后的 ``AppConfig`` 实例或枚举定义；其字段和方法共同承担上述职责。
    """
    timezone: str
    browser: BrowserConfig
    sources: list[SourceConfig]
    collection: CollectionConfig = field(default_factory=CollectionConfig)
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    monitor_sources: list[SourceConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        """处理：把全局采集排序默认值解析到每个未覆盖的来源。
        输入：
        - 无显式业务参数：读取 ``collection.item_order`` 以及正式、发现来源配置。
        输出：所有来源都得到已校验的具体 ``item_order``，供采集与监控路径一致执行。
        """
        validate_collection_config(self.collection)
        for source in [*self.sources, *self.monitor_sources]:
            if source.item_order is None:
                source.item_order = self.collection.item_order
            if source.item_order not in ITEM_ORDER_VALUES:
                raise ValueError(
                    f"Invalid item_order for {source.id!r}: use source or published_at"
                )

    def source_by_id(self, source_id: str) -> SourceConfig:
        """处理：按来源 ID 返回对应的来源配置。
        输入：
        - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
        输出：封装“按来源 ID 返回对应的来源配置”业务结果的 ``SourceConfig`` 对象；
          调用方据此继续相邻阶段或识别无结果状态。
        """
        for source in [*self.sources, *self.monitor_sources]:
            if source.id == source_id:
                return source
        raise KeyError(f"Unknown source: {source_id}")

    @property
    def all_monitor_sources(self) -> list[SourceConfig]:
        """处理：返回全部需要监控的来源配置。
        输入：
        - 无显式业务参数：不接收额外业务参数；
          从当前实例读取“返回全部需要监控的来源配置”所需状态；
          实现会明确读取属性 monitor_sources、sources。
        输出：按“返回全部需要监控的来源配置”规则得到的 ``SourceConfig`` 列表；
          列表顺序表达配置优先级、业务排名或稳定扫描顺序。
        """
        seen: set[str] = set()
        sources: list[SourceConfig] = []
        for source in [*self.sources, *self.monitor_sources]:
            if source.id in seen or not source.monitor_enabled:
                continue
            seen.add(source.id)
            sources.append(source)
        return sources


def validate_monitor_config(monitor: MonitorConfig) -> MonitorConfig:
    """处理：校验监控配置并在不满足约束时报告错误。
    输入：
    - ``monitor``：零模型监控的刷新、并发、缓存和聚类配置。
    输出：封装“校验监控配置并在不满足约束时报告错误”业务结果的 ``MonitorConfig`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    if monitor.request_timeout_seconds <= 0:
        raise ValueError("monitor.request_timeout_seconds must be positive")
    if monitor.global_concurrency <= 0 or monitor.per_domain_concurrency <= 0:
        raise ValueError("monitor concurrency values must be positive")
    if not 1024 <= monitor.max_feed_bytes <= 10 * 1024 * 1024:
        raise ValueError("monitor.max_feed_bytes must be between 1 KB and 10 MB")
    if not 1 <= monitor.max_items_per_feed <= 200:
        raise ValueError("monitor.max_items_per_feed must be between 1 and 200")
    if monitor.max_age_hours <= 0:
        raise ValueError("monitor.max_age_hours must be positive")
    if monitor.snapshot_max_age_minutes <= 0:
        raise ValueError("monitor.snapshot_max_age_minutes must be positive")
    if monitor.default_refresh_interval_minutes <= 0:
        raise ValueError("monitor.default_refresh_interval_minutes must be positive")
    if not 0.4 <= monitor.cluster_similarity_threshold <= 0.95:
        raise ValueError(
            "monitor.cluster_similarity_threshold must be between 0.4 and 0.95"
        )
    return monitor


def validate_collection_config(collection: CollectionConfig) -> CollectionConfig:
    """处理：校验全来源候选排序只能使用受支持的确定性模式。
    输入：
    - ``collection``：来自 sources.yaml 的 collection 段，提供默认候选排序值。
    输出：原采集配置；非法值在读取配置时立即报出，避免不同采集路径静默分叉。
    """
    if collection.item_order not in ITEM_ORDER_VALUES:
        raise ValueError("collection.item_order must be one of: source, published_at")
    return collection


def _load_source_rows(path: Path) -> list[dict[str, Any]]:
    """处理：读取 YAML 来源配置文件并返回 sources 数组中的原始来源记录。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：来源配置原始记录列表；每项是待构造 SourceConfig 的映射，
      包含来源身份、入口、分类、过滤规则、限额及可选采集设置。
    """
    if not path.exists():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if payload is None:
        return []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("sources", [])
    else:
        raise ValueError(f"Source bundle must be a YAML mapping or list: {path}")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"Source bundle requires a sources array of objects: {path}")
    return rows


def _validate_source(source: SourceConfig, budget: BudgetConfig) -> None:
    """处理：校验来源并在不满足约束时报告错误。
    输入：
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``budget``：运行时长、模型 Token、情境条目和全文提取预算。
    输出：不返回新数据；完成“校验来源并在不满足约束时报告错误”，
      副作用限于该处理声明的受控对象或产物。
    """
    validate_content_taxonomy(source.module, source.category)
    if source.tier not in {1, 2, 3}:
        raise ValueError(f"Invalid source tier for {source.id!r}: use 1, 2, or 3")
    if source.refresh_interval_minutes is not None and source.refresh_interval_minutes <= 0:
        raise ValueError(
            f"Invalid refresh_interval_minutes for {source.id!r}: require a positive value"
        )
    if source.feed_item_limit is not None and not 1 <= source.feed_item_limit <= 200:
        raise ValueError(
            f"Invalid feed_item_limit for {source.id!r}: require 1 through 200"
        )
    if source.item_order is not None and source.item_order not in ITEM_ORDER_VALUES:
        raise ValueError(
            f"Invalid item_order for {source.id!r}: use source or published_at"
        )
    for feed_url in source.feed_urls:
        parsed = urlsplit(feed_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Invalid feed URL for {source.id!r}: {feed_url!r}")
    if not 0 <= source.report_target <= source.report_max <= budget.report_items_per_source:
        raise ValueError(
            f"Invalid report target for {source.id!r}: require 0 <= report_target <= "
            f"report_max <= {budget.report_items_per_source}"
        )


def project_root() -> Path:
    """处理：返回当前仓库或安装包的根目录。
    输入：
    - 无显式业务参数：不接收参数；根据当前 config.py 的安装位置定位仓库或技能包根目录。
    输出：指向“返回当前仓库或安装包的根目录”所生成、定位或确认产物的本地路径。
    """
    explicit = environment_value("DAILY_INTEL_SKILL_DIR")
    hermes_home = environment_value("HERMES_HOME")
    local_app_data = environment_value("LOCALAPPDATA")
    if hermes_home:
        resolved_hermes_home = Path(hermes_home).expanduser().resolve()
    elif os.name == "nt" and local_app_data:
        resolved_hermes_home = (Path(local_app_data) / "hermes").resolve()
    else:
        resolved_hermes_home = (Path.home() / ".hermes").resolve()

    candidates = [
        Path(explicit).expanduser() if explicit else None,
        Path(__file__).resolve().parents[2],
        resolved_hermes_home / "skills" / "research" / "signaltrail",
        resolved_hermes_home / "skills" / "research" / "merak-brief",
        resolved_hermes_home / "skills" / "research" / "daily-intelligence",
        Path.cwd(),
    ]
    skills_dir = resolved_hermes_home / "skills"
    if skills_dir.exists():
        candidates.extend(skills_dir.glob("*/signaltrail"))
        candidates.extend(skills_dir.glob("*/merak-brief"))
        candidates.extend(skills_dir.glob("*/daily-intelligence"))

    checked: list[str] = []
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.resolve()
        checked.append(str(resolved))
        if (
            (resolved / "SKILL.md").is_file()
            and (resolved / "configs" / "sources.yaml").is_file()
            and (resolved / "schemas" / "report.schema.json").is_file()
        ):
            return resolved
    raise FileNotFoundError(
        "Cannot locate the SignalTrail skill resources. Set DAILY_INTEL_SKILL_DIR "
        "to the directory containing SKILL.md, configs/, and schemas/. Checked: "
        + ", ".join(checked)
    )


def load_config(path: Path | None = None, timezone: str | None = None) -> AppConfig:
    """处理：合并应用 YAML、来源文件和时区覆盖，校验后构造完整应用配置。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    - ``timezone``：IANA 时区名称；用于解析无时区时间并生成日报时间边界。
    输出：封装“合并应用 YAML、来源文件和时区覆盖，
      校验后构造完整应用配置”业务结果的 ``AppConfig`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    config_path = path or project_root() / "configs" / "sources.yaml"
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    browser = BrowserConfig(**raw.get("browser", {}))
    collection = validate_collection_config(
        CollectionConfig(**raw.get("collection", {}))
    )
    budget = BudgetConfig(**raw.get("budget", {}))
    output_values = dict(raw.get("output", {}))
    output_values.setdefault("copy_html_to_desktop", True)
    output = validate_output_config(OutputConfig(**output_values))
    media = validate_media_config(MediaConfig(**raw.get("media", {})))
    monitor = validate_monitor_config(MonitorConfig(**raw.get("monitor", {})))
    sources = [SourceConfig(**item) for item in raw.get("sources", [])]
    for source in sources:
        _validate_source(source, budget)
    monitor_sources: list[SourceConfig] = []
    if monitor.sources_file:
        bundle_path = (config_path.parent / monitor.sources_file).resolve()
        monitor_sources = [
            SourceConfig(
                **{
                    "report_target": 0,
                    "report_max": 0,
                    "bundle": "discovery",
                    **item,
                }
            )
            for item in _load_source_rows(bundle_path)
        ]
    configured_ids = [source.id for source in [*sources, *monitor_sources]]
    duplicate_ids = sorted(
        source_id for source_id in set(configured_ids) if configured_ids.count(source_id) > 1
    )
    if duplicate_ids:
        raise ValueError(f"Duplicate source IDs across core and monitor sources: {duplicate_ids}")
    for source in monitor_sources:
        _validate_source(source, budget)
    configured_timezone = timezone or raw.get("timezone", "Asia/Shanghai")
    return AppConfig(
        timezone=configured_timezone,
        browser=browser,
        sources=sources,
        collection=collection,
        budget=budget,
        output=output,
        media=media,
        monitor=monitor,
        monitor_sources=monitor_sources,
    )


def resolve_hermes_home(platform: str | None = None) -> Path:
    """处理：按显式参数、环境变量和平台默认值确定 Hermes 主目录。
    输入：
    - ``platform``：可注入的操作系统名称；为空时读取当前 Python 运行平台。
    输出：指向“按显式参数、环境变量和平台默认值确定 Hermes 主目录”所生成、定位或确认产物的本地路
      径。
    """
    value = environment_value("HERMES_HOME")
    if value:
        return Path(value).expanduser().resolve()
    if (platform or os.name) == "nt":
        local_app_data = environment_value("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / "hermes").resolve()
    return (Path.home() / ".hermes").resolve()


def resolve_data_dir(explicit: Path | None = None, *, allow_conflict: bool = False) -> Path:
    """处理：按显式参数、环境变量和配置确定唯一运行数据根目录。
    输入：
    - ``explicit``：调用方显式指定的路径或配置值；优先级高于环境变量和默认值。
    - ``allow_conflict``：是否允许显式数据根与既有 Hermes 绑定不同；默认拒绝以免分裂状态。
    输出：指向“按显式参数、环境变量和配置确定唯一运行数据根目录”所生成、定位或确认产物的本地路径
      。
    """
    value = environment_value("DAILY_INTEL_DATA_DIR")
    if explicit and value:
        resolved_explicit = explicit.expanduser().resolve()
        resolved_environment = Path(value).expanduser().resolve()
        if resolved_explicit != resolved_environment and not allow_conflict:
            raise ValueError(
                "--data-dir conflicts with DAILY_INTEL_DATA_DIR: "
                f"explicit={resolved_explicit}, environment={resolved_environment}. "
                "Use one canonical data root."
            )
    if explicit:
        return explicit.expanduser().resolve()
    if value:
        return Path(value).expanduser().resolve()
    return (resolve_hermes_home() / "daily-intelligence").resolve()


def resolve_profile_dir(config: AppConfig, explicit: Path | None = None) -> Path:
    """处理：确定持久化浏览器 Profile 目录并规范为绝对路径。
    输入：
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``explicit``：调用方显式指定的路径或配置值；优先级高于环境变量和默认值。
    输出：指向“确定持久化浏览器 Profile 目录并规范为绝对路径”所生成、定位或确认产物的本地路径。
    """
    if explicit:
        return explicit.expanduser().resolve()
    value = environment_value(config.browser.profile_dir_env)
    if value:
        return Path(value).expanduser().resolve()
    return (resolve_hermes_home() / "browser-profiles" / "daily-intelligence").resolve()


def resolve_browser_channel(
    config: AppConfig,
    explicit: str | None = None,
    platform: str | None = None,
) -> str | None:
    """处理：按显式参数、环境变量和配置选择 Playwright 浏览器通道。
    输入：
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``explicit``：调用方显式指定的路径或配置值；优先级高于环境变量和默认值。
    - ``platform``：可注入的操作系统名称；为空时读取当前 Python 运行平台。
    输出：封装“按显式参数、环境变量和配置选择 Playwright 浏览器通道”业务结果的 ``str | None`` 对
      象；调用方据此继续相邻阶段或识别无结果状态。
    """
    if explicit is not None:
        return explicit or None
    value = environment_value(config.browser.channel_env)
    if value is not None:
        return value or None
    if config.browser.default_channel:
        return config.browser.default_channel
    return "msedge" if (platform or os.name) == "nt" else None


def canonical_source_page_url(source_id: str, url: str) -> str:
    """处理：升级已知过期索引页地址，同时不修改不可变旧索引。
    输入：
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：经过选择、规范化或安全处理的 URL 字符串，供后续访问或渲染使用。
    """
    return SOURCE_PAGE_REWRITES.get((source_id, url.rstrip("/")), url)


def _source_pages_path(data_dir: Path) -> Path:
    """处理：返回运行时来源扩展页面配置文件的位置。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“返回运行时来源扩展页面配置文件的位置”所生成、定位或确认产物的本地路径。
    """
    return data_dir / "state" / "source-pages.json"


def load_source_pages(data_dir: Path) -> list[dict[str, Any]]:
    """处理：读取运行时扩展来源页 JSON，并忽略损坏、重复或字段不完整的记录。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：“读取运行时扩展来源页 JSON，并忽略损坏、重复或字段不完整的记录”得到的有序结构化记录；
      每项承载处理说明所定义的身份、证据或状态字段，可直接交给下一阶段。
    """
    path = _source_pages_path(data_dir)
    if not path.exists():
        return []
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError(f"Invalid dynamic source page registry: {path}")
    return [item for item in payload["items"] if isinstance(item, dict)]


def validate_source_page(source: SourceConfig, url: str) -> str:
    """处理：校验来源页面并在不满足约束时报告错误。
    输入：
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：“校验来源页面并在不满足约束时报告错误”得到的规范字符串，供调用方存储、比较或展示。
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Source page URL must be an absolute HTTP(S) URL")
    hostname = parsed.netloc.lower().removeprefix("www.")
    base_hostname = urlsplit(source.url).netloc.lower().removeprefix("www.")
    allowed = set(source.include_domains) | {base_hostname}
    if not any(hostname == domain or hostname.endswith(f".{domain}") for domain in allowed):
        raise ValueError(
            f"Source page host {hostname!r} is outside configured domains for {source.id!r}"
        )
    return url


def add_source_page(
    config: AppConfig,
    data_dir: Path,
    source_id: str,
    url: str,
    reason: str,
) -> Path:
    """处理：校验来源与 URL 后，把扩展页面写入运行时配置。
    输入：
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    - ``reason``：创建或打开额外来源页面的审计原因；写入页面追踪记录。
    输出：指向“校验来源与 URL 后，把扩展页面写入运行时配置”所生成、定位或确认产物的本地路径。
    """
    source = config.source_by_id(source_id)
    url = validate_source_page(source, url)
    items = load_source_pages(data_dir)
    retained = [
        item
        for item in items
        if not (item.get("source_id") == source_id and item.get("url") == url)
    ]
    if sum(item.get("source_id") == source_id for item in retained) >= 5:
        raise ValueError(f"Dynamic page limit reached for {source_id!r}; remove one before adding")
    retained.append(
        {
            "source_id": source_id,
            "url": url,
            "reason": reason,
            "status": "approved",
            "added_at": now_iso(config.timezone),
        }
    )
    path = _source_pages_path(data_dir)
    write_json(path, {"schema_version": "1.0", "items": retained})
    return path


def remove_source_page(data_dir: Path, source_id: str, url: str) -> Path:
    """处理：从运行时配置移除指定来源页面并持久化结果。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``source_id``：来源的稳定 ID；用于配置查找、索引关联和状态分区。
    - ``url``：调用方提供的 URL；当前函数按处理说明进行规范化、过滤或访问。
    输出：指向“从运行时配置移除指定来源页面并持久化结果”所生成、定位或确认产物的本地路径。
    """
    retained = [
        item
        for item in load_source_pages(data_dir)
        if not (item.get("source_id") == source_id and item.get("url") == url)
    ]
    path = _source_pages_path(data_dir)
    write_json(path, {"schema_version": "1.0", "items": retained})
    return path


def source_urls(source: SourceConfig, data_dir: Path) -> list[str]:
    """处理：合并来源主页、探索页和运行时扩展页并保持去重顺序。
    输入：
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：“合并来源主页、探索页和运行时扩展页并保持去重顺序”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    dynamic = [
        str(item["url"])
        for item in load_source_pages(data_dir)
        if item.get("source_id") == source.id and item.get("status") == "approved"
    ]
    urls = [source.url, *source.explore_urls, *dynamic]
    return list(dict.fromkeys(validate_source_page(source, url) for url in urls))
