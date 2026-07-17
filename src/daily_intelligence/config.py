from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from .taxonomy import validate_content_taxonomy
from .utils import now_iso, read_json, write_json

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
    module: str = "information"
    category: str = "international"
    language: str = "en"
    region: str = "global"
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
class AppConfig:
    timezone: str
    browser: BrowserConfig
    sources: list[SourceConfig]
    budget: BudgetConfig = field(default_factory=BudgetConfig)

    def source_by_id(self, source_id: str) -> SourceConfig:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(f"Unknown source: {source_id}")


def project_root() -> Path:
    explicit = os.getenv("DAILY_INTEL_SKILL_DIR")
    hermes_home = os.getenv("HERMES_HOME")
    if hermes_home:
        resolved_hermes_home = Path(hermes_home).expanduser().resolve()
    elif os.name == "nt" and os.getenv("LOCALAPPDATA"):
        resolved_hermes_home = (Path(os.environ["LOCALAPPDATA"]) / "hermes").resolve()
    else:
        resolved_hermes_home = (Path.home() / ".hermes").resolve()

    candidates = [
        Path(explicit).expanduser() if explicit else None,
        Path(__file__).resolve().parents[2],
        resolved_hermes_home / "skills" / "research" / "daily-intelligence",
        Path.cwd(),
    ]
    skills_dir = resolved_hermes_home / "skills"
    if skills_dir.exists():
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
        "Cannot locate the daily-intelligence skill resources. Set DAILY_INTEL_SKILL_DIR "
        "to the directory containing SKILL.md, configs/, and schemas/. Checked: "
        + ", ".join(checked)
    )


def load_config(path: Path | None = None, timezone: str | None = None) -> AppConfig:
    config_path = path or project_root() / "configs" / "sources.yaml"
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    browser = BrowserConfig(**raw.get("browser", {}))
    budget = BudgetConfig(**raw.get("budget", {}))
    sources = [SourceConfig(**item) for item in raw.get("sources", [])]
    for source in sources:
        validate_content_taxonomy(source.module, source.category)
        if not 0 <= source.report_target <= source.report_max <= budget.report_items_per_source:
            raise ValueError(
                f"Invalid report target for {source.id!r}: require 0 <= report_target <= "
                f"report_max <= {budget.report_items_per_source}"
            )
    configured_timezone = timezone or raw.get("timezone", "Asia/Shanghai")
    return AppConfig(
        timezone=configured_timezone,
        browser=browser,
        sources=sources,
        budget=budget,
    )


def resolve_hermes_home(platform: str | None = None) -> Path:
    value = os.getenv("HERMES_HOME")
    if value:
        return Path(value).expanduser().resolve()
    if (platform or os.name) == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / "hermes").resolve()
    return (Path.home() / ".hermes").resolve()


def resolve_data_dir(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
    value = os.getenv("DAILY_INTEL_DATA_DIR")
    if value:
        return Path(value).expanduser().resolve()
    return (resolve_hermes_home() / "data" / "daily-intelligence").resolve()


def resolve_profile_dir(config: AppConfig, explicit: Path | None = None) -> Path:
    if explicit:
        return explicit.expanduser().resolve()
    value = os.getenv(config.browser.profile_dir_env)
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
    value = os.getenv(config.browser.channel_env)
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
