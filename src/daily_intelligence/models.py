from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class SourceStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    VERIFICATION_REQUIRED = "verification_required"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    NO_ITEMS = "no_items"


class ContentStatus(StrEnum):
    NOT_FETCHED = "not_fetched"
    FULL_TEXT = "full_text"
    PARTIAL = "partial"
    METADATA_ONLY = "metadata_only"
    VERIFICATION_REQUIRED = "verification_required"
    FAILED = "failed"


@dataclass(slots=True)
class ArticleItem:
    item_id: str
    source_id: str
    source_name: str
    title: str
    url: str
    canonical_url: str
    discovered_at: str
    module: str = "information"
    category: str = "international"
    content_status: str = ContentStatus.NOT_FETCHED
    description: str = ""
    published_at: str | None = None
    original_provider: str | None = None
    image_url: str | None = None
    content_path: str | None = None
    content_characters: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceResult:
    source_id: str
    source_name: str
    source_url: str
    status: str
    collected_at: str
    module: str = "information"
    category: str = "international"
    page_title: str = ""
    final_url: str = ""
    http_status: int | None = None
    error: str | None = None
    challenge: dict[str, Any] = field(default_factory=dict)
    page_results: list[dict[str, Any]] = field(default_factory=list)
    items: list[ArticleItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["items_count"] = len(self.items)
        return data
