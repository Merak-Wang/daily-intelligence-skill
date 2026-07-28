from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .localization import validate_output_language
from .taxonomy import validate_content_taxonomy
from .utils import environment_value, now_iso, read_json, write_json

SOURCE_PAGE_REWRITES = {
    ("huggingface_papers", "https://huggingface.co/papers/month"): (
        "https://huggingface.co/papers"
    ),
}


@dataclass(slots=True)
class SourceConfig:
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
    report_target: int = 10
    report_max: int = 15
    wait_ms: int | None = None

    @property
    def adapter_name(self) -> str:
        return self.adapter or self.mode


@dataclass(slots=True)
class BrowserConfig:
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
class BudgetConfig:
    max_runtime_seconds: int = 600
    max_agent_tokens: int = 10_000_000
    context_items_per_source: int = 25
    report_items_per_source: int = 15
    max_fulltext_per_run: int = 12


@dataclass(slots=True)
class OutputConfig:
    language: str = "zh-CN"
    formats: list[str] = field(default_factory=lambda: ["html", "pdf"])
    pdf_engine: str = "edge"
    open_after_finalize: bool = False
    copy_html_to_desktop: bool = False
    desktop_dir: str | None = None


@dataclass(slots=True)
class MediaConfig:
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
    timezone: str
    browser: BrowserConfig
    sources: list[SourceConfig]
    budget: BudgetConfig = field(default_factory=BudgetConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    media: MediaConfig = field(default_factory=MediaConfig)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    monitor_sources: list[SourceConfig] = field(default_factory=list)

    def source_by_id(self, source_id: str) -> SourceConfig:
        for source in [*self.sources, *self.monitor_sources]:
            if source.id == source_id:
                return source
        raise KeyError(f"Unknown source: {source_id}")

    @property
    def all_monitor_sources(self) -> list[SourceConfig]:
        seen: set[str] = set()
        sources: list[SourceConfig] = []
        for source in [*self.sources, *self.monitor_sources]:
            if source.id in seen or not source.monitor_enabled:
                continue
            seen.add(source.id)
            sources.append(source)
        return sources


def validate_monitor_config(monitor: MonitorConfig) -> MonitorConfig:
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


def _load_source_rows(path: Path) -> list[dict[str, Any]]:
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
    config_path = path or project_root() / "configs" / "sources.yaml"
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    browser = BrowserConfig(**raw.get("browser", {}))
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
        budget=budget,
        output=output,
        media=media,
        monitor=monitor,
        monitor_sources=monitor_sources,
    )


def resolve_hermes_home(platform: str | None = None) -> Path:
    value = environment_value("HERMES_HOME")
    if value:
        return Path(value).expanduser().resolve()
    if (platform or os.name) == "nt":
        local_app_data = environment_value("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / "hermes").resolve()
    return (Path.home() / ".hermes").resolve()


def resolve_data_dir(explicit: Path | None = None, *, allow_conflict: bool = False) -> Path:
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
    if explicit is not None:
        return explicit or None
    value = environment_value(config.browser.channel_env)
    if value is not None:
        return value or None
    if config.browser.default_channel:
        return config.browser.default_channel
    return "msedge" if (platform or os.name) == "nt" else None


def canonical_source_page_url(source_id: str, url: str) -> str:
    """Upgrade known obsolete index pages without mutating immutable legacy indexes."""
    return SOURCE_PAGE_REWRITES.get((source_id, url.rstrip("/")), url)


def _source_pages_path(data_dir: Path) -> Path:
    return data_dir / "state" / "source-pages.json"


def load_source_pages(data_dir: Path) -> list[dict[str, Any]]:
    path = _source_pages_path(data_dir)
    if not path.exists():
        return []
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError(f"Invalid dynamic source page registry: {path}")
    return [item for item in payload["items"] if isinstance(item, dict)]


def validate_source_page(source: SourceConfig, url: str) -> str:
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
    retained = [
        item
        for item in load_source_pages(data_dir)
        if not (item.get("source_id") == source_id and item.get("url") == url)
    ]
    path = _source_pages_path(data_dir)
    write_json(path, {"schema_version": "1.0", "items": retained})
    return path


def source_urls(source: SourceConfig, data_dir: Path) -> list[str]:
    dynamic = [
        str(item["url"])
        for item in load_source_pages(data_dir)
        if item.get("source_id") == source.id and item.get("status") == "approved"
    ]
    urls = [source.url, *source.explore_urls, *dynamic]
    return list(dict.fromkeys(validate_source_page(source, url) for url in urls))
