from __future__ import annotations

from enum import StrEnum


class ContentModule(StrEnum):
    INFORMATION = "information"
    TECHNOLOGY = "technology"


class InformationCategory(StrEnum):
    INTERNATIONAL = "international"
    DOMESTIC = "domestic"
    MILITARY = "military"
    MARKET = "market"
    # Legacy categories remain readable for schema 1.1/1.2 reports and source indexes.
    ECONOMY = "economy"
    TECHNOLOGY = "technology"


class TechnologyCategory(StrEnum):
    NEWS = "news"
    PAPERS = "papers"
    OPEN_SOURCE = "open_source"


class AnalysisDomain(StrEnum):
    GEOPOLITICS = "geopolitics"
    MARKETS = "markets"
    AI_TECHNOLOGY = "ai_technology"


CATEGORIES_BY_MODULE: dict[str, set[str]] = {
    ContentModule.INFORMATION: {item.value for item in InformationCategory},
    ContentModule.TECHNOLOGY: {item.value for item in TechnologyCategory},
}

REQUIRED_SECTION_IDS_V11 = {
    "information.international",
    "information.domestic",
    "information.military",
    "information.economy",
    "technology.news",
    "technology.papers",
    "technology.open_source",
}
REQUIRED_SECTION_IDS_V12 = REQUIRED_SECTION_IDS_V11 | {"information.technology"}
REQUIRED_SECTION_IDS_V13 = {
    "information.international",
    "information.domestic",
    "information.military",
    "information.market",
    "technology.news",
    "technology.papers",
    "technology.open_source",
}

SECTION_ORDER_V13 = (
    "information.international",
    "information.domestic",
    "information.military",
    "information.market",
    "technology.news",
    "technology.papers",
    "technology.open_source",
)

SECTION_GROUPS_V13 = {
    "information": SECTION_ORDER_V13[:4],
    "technology": SECTION_ORDER_V13[4:],
}

SECTION_TITLES_V13 = {
    "information.international": "国际",
    "information.domestic": "国内新闻",
    "information.military": "军事",
    "information.market": "市场",
    "technology.news": "技术新闻",
    "technology.papers": "值得阅读的论文",
    "technology.open_source": "今日值得关注的开源项目",
}

# Model-authored drafts from earlier skill revisions used these intuitive names. Keep
# accepting them at the draft boundary, but always compile the persisted report to the
# canonical schema 1.5 identifiers above.
SECTION_ID_ALIASES_V15 = {
    "information.economy": "information.market",
    "information.markets": "information.market",
    "technology.tech_news": "technology.news",
    "technology.technology_news": "technology.news",
    "technology.oss": "technology.open_source",
    "technology.opensource": "technology.open_source",
}


def canonical_section_id(section_id: str) -> str:
    """Return the schema 1.5 section ID for a canonical or legacy draft ID."""
    normalized = section_id.strip()
    return SECTION_ID_ALIASES_V15.get(normalized, normalized)


def required_section_ids(schema_version: str | None) -> set[str]:
    if schema_version in {"1.3", "1.4", "1.5"}:
        return REQUIRED_SECTION_IDS_V13
    return REQUIRED_SECTION_IDS_V12 if schema_version == "1.2" else REQUIRED_SECTION_IDS_V11


def validate_content_taxonomy(module: str, category: str) -> None:
    allowed = CATEGORIES_BY_MODULE.get(module)
    if allowed is None:
        raise ValueError(f"Unknown content module: {module}")
    if category not in allowed:
        raise ValueError(f"Category {category!r} is not valid for module {module!r}")
