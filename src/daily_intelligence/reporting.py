from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator, FormatChecker

from .config import canonical_source_page_url, project_root
from .semantics import reusable_semantic_brief, semantic_fingerprint
from .taxonomy import (
    SECTION_ID_ALIASES_V15,
    SECTION_ORDER_V13,
    SECTION_TITLES_V13,
    canonical_section_id,
    required_section_ids,
    validate_content_taxonomy,
)
from .utils import canonicalize_url, now_iso, read_json

MAX_FEATURED_EVENTS = 12
IMPORTANCE_CAPS = {
    "impact": 30,
    "freshness": 15,
    "relevance": 15,
    "source_quality": 15,
    "corroboration": 10,
    "novelty": 10,
    "continuity": 5,
}
ANALYSIS_DOMAIN_REQUIREMENTS = {
    "geopolitics": {
        "perspectives": ["geopolitics", "china_standpoint", "western_standpoint"],
        "assessment_types": ["trend", "risk"],
    },
    "ai_technology": {
        "perspectives": ["ai_research_engineering"],
        "assessment_types": ["trend", "learning_research"],
    },
    "markets": {
        "perspectives": ["equity_analysis"],
        "assessment_types": ["trend", "risk"],
    },
}

CONTENT_STATUS_TO_ACCESS = {
    "not_fetched": "metadata_only",
    "failed": "metadata_only",
    "metadata_only": "metadata_only",
    "partial": "partial",
    "full_text": "full_text",
    "verification_required": "verification_required",
}

_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_NUMERIC_SCENARIO_PATTERN = re.compile(
    r"(?:\d+(?:\.\d+)?\s*%|[$¥￥]\s*\d|\d+(?:\.\d+)?\s*(?:美元|元|亿|万亿)|"
    r"\d+(?:\.\d+)?\s*[-—–至]\s*\d+(?:\.\d+)?)"
)
_LANGUAGE_MARKER_PATTERN = re.compile(r"^\s*\[(?:英|英文|EN)\]\s*", re.I)
_TRANSLATION_PREFIX_PATTERN = re.compile(
    r"^\s*(?:\[[^\]\r\n]{1,40}\]|【[^】\r\n]{1,40}】)\s*[:：-]?\s*"
)
_TLDR_BOILERPLATE_PATTERNS = (
    re.compile(r"^\s*来源[：:]"),
    re.compile(r"^\s*来源.{0,80}(?:报道|消息)[。.!]?\s*$", re.I),
    re.compile(r"^\s*(?:详见|请见)(?:原文)?(?:链接|报道)"),
    re.compile(r"来源原文标题|原文标题[：:]"),
    re.compile(r"暂未获取中文摘要|待补写|需由生成\s*Agent", re.I),
    re.compile(r"(?:尚未|暂未|未)获取(?:到)?(?:正文|中文摘要)"),
    re.compile(r"仅取得(?:来源)?标题或公开元数据"),
    re.compile(r"正文尚未读取.{0,80}(?:原文链接|完整内容)"),
    re.compile(r"仅依据标题(?:和|与)?(?:来源信息|元数据)?.*记录"),
    re.compile(r"关于.{0,100}(?:详细报道|相关报道)"),
)
_UNREAD_BODY_MARKERS = ("未读取正文", "正文尚未读取", "仅依据标题", "仅依据元数据")
EVALUATION_DIMENSIONS = {
    "coverage",
    "importance_ordering",
    "factual_reliability",
    "summary_accuracy",
    "analysis_traceability",
    "historical_continuity",
    "readability",
    "timeliness",
    "compliance_boundaries",
}
REQUIRED_PERSPECTIVES = {
    "geopolitics",
    "ai_research_engineering",
    "equity_analysis",
    "china_standpoint",
    "western_standpoint",
}


def evaluation_continuity_floor(
    evaluation: dict[str, Any],
) -> tuple[str, set[str], str | None]:
    """Apply deterministic contamination guards to an evaluator's reuse decision."""
    decision = str(evaluation.get("continuity_decision", "selective"))
    raw_excluded = evaluation.get("exclude_from_continuity", [])
    excluded = set(raw_excluded) if isinstance(raw_excluded, list) else set()
    dimensions = evaluation.get("dimensions", [])
    scores_by_id = {
        str(item.get("id")): int(item["score"])
        for item in dimensions
        if isinstance(item, dict) and isinstance(item.get("score"), int)
    }
    critical_ids = {
        "factual_reliability",
        "summary_accuracy",
        "analysis_traceability",
        "compliance_boundaries",
    }
    low_critical = sorted(
        dimension_id
        for dimension_id in critical_ids
        if scores_by_id.get(dimension_id, 5) <= 2
    )
    total_score = evaluation.get("total_score")
    reject_required = (
        isinstance(total_score, int) and total_score <= 22
    ) or len(low_critical) >= 3
    if reject_required:
        if decision == "reject" and "all" in excluded:
            return decision, excluded, None
        return (
            "reject",
            {"all"},
            "continuity must reject all content when total_score <= 22 or at least "
            "three critical dimensions score <= 2",
        )

    required_exclusions: set[str] = set()
    if scores_by_id.get("summary_accuracy", 5) <= 2:
        required_exclusions.add("event_summaries")
    if scores_by_id.get("factual_reliability", 5) <= 2:
        required_exclusions.add("source_access")
    if decision == "accept" and (
        (isinstance(total_score, int) and total_score < 32) or low_critical
    ):
        if not required_exclusions:
            required_exclusions.update({"event_summaries", "analyses"})
        return (
            "selective",
            excluded | required_exclusions,
            "continuity cannot accept a report with total_score < 32 or a critical "
            f"dimension score <= 2: {low_critical}",
        )
    if decision == "selective":
        effective_excluded = excluded | required_exclusions
        if not effective_excluded:
            effective_excluded.add("event_summaries")
        if effective_excluded != excluded:
            return (
                decision,
                effective_excluded,
                "selective continuity requires explicit exclusions for low-quality fields",
            )
    return decision, excluded, None


def content_status_to_access(status: str | None) -> str | None:
    if status is None:
        return None
    return CONTENT_STATUS_TO_ACCESS.get(str(status))


def _require_chinese(value: object, location: str, errors: list[str]) -> None:
    if isinstance(value, str) and value.strip() and not _CJK_PATTERN.search(value):
        errors.append(f"{location}: published user-facing text must contain Chinese")


def _normalize_brief_title(brief: dict[str, Any], indexed: dict[str, Any]) -> None:
    """Keep the indexed headline verbatim and preserve an authored Chinese translation."""
    original_title = str(indexed.get("title") or "").strip()
    if not original_title:
        return
    drafted_title = _LANGUAGE_MARKER_PATTERN.sub(
        "", str(brief.get("title") or "").strip()
    )
    drafted_translation = _TRANSLATION_PREFIX_PATTERN.sub(
        "", str(brief.get("title_zh") or "").strip()
    )
    brief["title"] = original_title
    if _CJK_PATTERN.search(original_title):
        brief.pop("title_zh", None)
        return
    if not _CJK_PATTERN.search(drafted_translation) and _CJK_PATTERN.search(drafted_title):
        drafted_translation = drafted_title
    if _CJK_PATTERN.search(drafted_translation):
        brief["title_zh"] = drafted_translation
    else:
        brief.pop("title_zh", None)


def _tldr_quality_issue(value: object, title: str) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "TL;DR is empty"
    text = value.strip()
    for pattern in _TLDR_BOILERPLATE_PATTERNS:
        if pattern.search(text):
            return "TL;DR is boilerplate instead of a Chinese summary of observed content"
    if len(_CJK_PATTERN.findall(text)) < 4:
        return (
            "TL;DR must contain a substantive Chinese sentence, not an English abstract "
            "with a Chinese prefix"
        )
    normalized_text = re.sub(r"[\s\W_]+", "", text)
    normalized_title = re.sub(r"[\s\W_]+", "", title)
    if normalized_title and normalized_text == normalized_title:
        return "TL;DR merely repeats the headline"
    return None


def _schema_errors(report: object) -> list[str]:
    schema = read_json(project_root() / "schemas" / "report.schema.json")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors: list[str] = []
    for error in sorted(validator.iter_errors(report), key=lambda item: list(item.absolute_path)):
        location = "$"
        for part in error.absolute_path:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        errors.append(f"{location}: {error.message}")
    return errors


def _publication_date(value: object, timezone: str) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text)
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(ZoneInfo(timezone))
        return parsed.date()
    except (ValueError, KeyError):
        return None


def _freshness_cap(age_days: int) -> int:
    if age_days <= 0:
        return 15
    if age_days == 1:
        return 13
    if age_days <= 3:
        return 8
    if age_days <= 7:
        return 4
    return 1


def hydrate_report_evidence(report: dict, index: dict | None) -> None:
    if not isinstance(index, dict):
        return
    sources = {
        row.get("source_id"): row
        for row in index.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }
    items = {
        row.get("item_id"): row
        for row in index.get("items", [])
        if isinstance(row, dict) and row.get("item_id")
    }
    for section in report.get("sections", []):
        for brief in section.get("briefs", []):
            indexed = items.get(brief.get("item_id"), {})
            if not indexed:
                continue
            _normalize_brief_title(brief, indexed)
            source = sources.get(indexed.get("source_id"), {})
            ref = brief.setdefault("source_ref", {})
            for key, value in (
                ("item_id", indexed.get("item_id")),
                ("title", indexed.get("title")),
                ("url", indexed.get("url")),
                ("access", content_status_to_access(indexed.get("content_status"))),
                ("role", indexed.get("metadata", {}).get("role", "discovery")),
                ("published_at", indexed.get("published_at")),
            ):
                if value is not None:
                    ref.setdefault(key, value)
            brief.setdefault(
                "primary_source",
                {
                    "id": indexed.get("source_id", "unknown"),
                    "name": indexed.get("source_name", "未知来源"),
                    "url": source.get("source_url") or indexed.get("url"),
                },
            )
        for event in section.get("items", []):
            for ref in event.get("source_refs", []):
                indexed = items.get(ref.get("item_id"), {})
                source = sources.get(indexed.get("source_id"), {})
                for key in ("published_at", "source_id", "source_name"):
                    if indexed.get(key):
                        ref[key] = indexed[key]
                if source.get("source_url"):
                    ref["source_url"] = source["source_url"]
            if not event.get("primary_source") and event.get("source_refs"):
                ref = event["source_refs"][0]
                event["primary_source"] = {
                    "id": ref.get("source_id", "unknown"),
                    "name": ref.get("source_name") or ref.get("title", "未知来源"),
                    "url": ref.get("source_url") or ref.get("url"),
                }


def _allocate_importance(total: int, freshness_cap: int) -> tuple[int, dict[str, int]]:
    caps = {**IMPORTANCE_CAPS, "freshness": freshness_cap}
    total = max(0, min(int(total), sum(caps.values())))
    cap_total = sum(caps.values())
    raw = {key: total * cap / cap_total for key, cap in caps.items()}
    result = {key: min(caps[key], int(value)) for key, value in raw.items()}
    remainder = total - sum(result.values())
    order = sorted(
        caps,
        key=lambda key: (raw[key] - int(raw[key]), caps[key]),
        reverse=True,
    )
    while remainder:
        progressed = False
        for key in order:
            if result[key] >= caps[key]:
                continue
            result[key] += 1
            remainder -= 1
            progressed = True
            if not remainder:
                break
        if not progressed:
            break
    return total, result


def _source_rank_label(source_id: str, rank: int) -> str:
    if source_id == "weibo_hot":
        return f"热搜Top{rank}"
    if source_id in {"hacker_news", "lobsters", "github_trending"}:
        return f"榜单Top{rank}"
    return f"来源Top{rank}"


def _pending_from_index(index: dict) -> list[dict[str, str]]:
    pending: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in index.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id", ""))
        source_name = str(source.get("source_name", source_id))
        page_rows = []
        for page in source.get("page_results", []):
            if not isinstance(page, dict):
                continue
            status = str(page.get("status", ""))
            if status == "no_items" and (
                page.get("error")
                or (
                    isinstance(page.get("http_status"), int)
                    and int(page["http_status"]) >= 400
                )
            ):
                page = {**page, "status": "failed"}
                status = "failed"
            if status in {"verification_required", "rate_limited", "failed"}:
                page_rows.append(page)
        source_status = str(source.get("status", ""))
        if source_status == "no_items" and source.get("error"):
            source_status = "failed"
        if not page_rows and source_status in {
            "verification_required",
            "rate_limited",
            "failed",
        }:
            page_rows = [
                {
                    "status": source_status,
                    "url": source.get("source_url"),
                    "error": source.get("error"),
                }
            ]
        for page in page_rows:
            url = canonical_source_page_url(
                source_id,
                str(page.get("url") or source.get("source_url") or ""),
            )
            if not url.startswith(("http://", "https://")) or (source_id, url) in seen:
                continue
            seen.add((source_id, url))
            status = str(page.get("status"))
            if status == "rate_limited":
                note = "来源暂时限制访问；本版保留链接，停止自动重试并等待后续时段"
            elif status == "verification_required":
                note = "需要人工验证；可从验证链接队列打开"
            else:
                note = "采集失败；保留链接供人工打开"
            pending.append(
                {
                    "source_id": source_id,
                    "source_name": source_name,
                    "status": status,
                    "note": note,
                    "url": url,
                }
            )
    return pending


def _normalize_draft_sections(value: object) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Normalize list/mapping draft sections and known model-authored aliases."""
    warnings: list[str] = []
    if value is None:
        entries: list[tuple[str | None, object]] = []
    elif isinstance(value, list):
        entries = [(None, section) for section in value]
    elif isinstance(value, dict):
        entries = [(str(section_id), section) for section_id, section in value.items()]
        warnings.append("sections object mapping was normalized to the canonical section array")
    else:
        raise ValueError(
            "Report draft sections must be an array or an object keyed by section ID"
        )

    normalized: dict[str, dict[str, Any]] = {}
    invalid: list[str] = []
    for position, (mapping_id, raw_section) in enumerate(entries):
        if not isinstance(raw_section, dict):
            raise ValueError(f"Report draft sections[{position}] must be an object")
        draft_id = str(
            raw_section.get("id")
            or mapping_id
            or f"{raw_section.get('module', '')}.{raw_section.get('category', '')}"
        ).strip(".")
        section_id = canonical_section_id(draft_id)
        if section_id not in SECTION_ORDER_V13:
            invalid.append(draft_id or f"sections[{position}]")
            continue
        if section_id != draft_id:
            warnings.append(f"section ID {draft_id!r} was normalized to {section_id!r}")

        target = normalized.setdefault(section_id, {"id": section_id, "briefs": [], "items": []})
        for collection in ("briefs", "items"):
            rows = raw_section.get(collection, [])
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise ValueError(
                    f"Report draft section {draft_id!r}.{collection} must be an array"
                )
            target[collection].extend(rows)
        for key, item in raw_section.items():
            if key not in {"id", "module", "category", "title", "briefs", "items"}:
                target[key] = item

    if invalid:
        allowed = ", ".join(SECTION_ORDER_V13)
        aliases = ", ".join(sorted(SECTION_ID_ALIASES_V15))
        raise ValueError(
            "Unsupported report draft section IDs: "
            f"{sorted(invalid)}. Use one of: {allowed}. Accepted legacy aliases: {aliases}"
        )
    return normalized, warnings


def _normalize_draft_analyses(value: object) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalize list/mapping analysis drafts without inventing semantic content."""
    warnings: list[str] = []
    if value is None:
        return [], warnings
    if isinstance(value, list):
        rows = value
    elif isinstance(value, dict):
        rows = []
        for domain, raw_analysis in value.items():
            if not isinstance(raw_analysis, dict):
                raise ValueError(f"Report draft analysis {domain!r} must be an object")
            analysis = dict(raw_analysis)
            analysis.setdefault("domain", str(domain))
            rows.append(analysis)
        warnings.append("analyses object mapping was normalized to the canonical analysis array")
    else:
        raise ValueError("Report draft analyses must be an array or an object keyed by domain")
    if not all(isinstance(analysis, dict) for analysis in rows):
        raise ValueError("Every report draft analysis entry must be an object")
    return [dict(analysis) for analysis in rows], warnings


def compile_report_data(
    report: dict,
    index: dict,
    semantic_cache: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Compile model-authored semantics into the deterministic schema 1.5 envelope."""
    warnings: list[str] = []
    report.setdefault("schema_version", "1.5")
    if report.get("schema_version") != "1.5":
        return warnings
    report.setdefault("date", index.get("date"))
    report.setdefault("edition", index.get("edition"))
    edition_label = "晚报" if report.get("edition") == "evening" else "晨报"
    if not report.get("title"):
        report["title"] = f"每日情报{edition_label} — {report.get('date', '')}"
        warnings.append("missing draft title was filled with the deterministic report title")
    summary = report.get("executive_summary")
    if isinstance(summary, str):
        report["executive_summary"] = [summary]
        warnings.append("executive_summary string was normalized to an array")
    elif not isinstance(summary, list):
        report["executive_summary"] = ["本版重点见以下资讯、技术与研判。"]
        warnings.append("missing executive_summary was filled with a neutral fallback")
    report["analyses"], analysis_warnings = _normalize_draft_analyses(
        report.get("analyses")
    )
    warnings.extend(analysis_warnings)
    provided_sections, section_warnings = _normalize_draft_sections(report.get("sections"))
    warnings.extend(section_warnings)
    report["language"] = "zh-CN"
    report["generated_at"] = now_iso(str(index.get("timezone", "Asia/Shanghai")))
    report["evaluation_status"] = "pending"
    report.pop("quality_evaluation", None)
    report.setdefault("changes", [])
    report.setdefault("tomorrow_watch_items", [])
    report["pending_verifications"] = _pending_from_index(index)

    sources = {
        str(row.get("source_id")): row
        for row in index.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }
    indexed_items = {
        str(row.get("item_id")): row
        for row in index.get("items", [])
        if isinstance(row, dict) and row.get("item_id")
    }
    if semantic_cache:
        existing_ids = {
            str(brief.get("item_id"))
            for section in provided_sections.values()
            for brief in section.get("briefs", [])
            if isinstance(brief, dict) and brief.get("item_id")
        }
        source_counts = Counter(
            str(indexed_items[item_id].get("source_id"))
            for item_id in existing_ids
            if item_id in indexed_items
        )
        source_policies = index.get("source_policies", {})
        for indexed in index.get("items", []):
            if not isinstance(indexed, dict) or not indexed.get("item_id"):
                continue
            item_id = str(indexed["item_id"])
            source_id = str(indexed.get("source_id") or "")
            policy = source_policies.get(source_id, {}) if isinstance(source_policies, dict) else {}
            target = int(policy.get("report_target", 0)) if isinstance(policy, dict) else 0
            if item_id in existing_ids or source_counts[source_id] >= target:
                continue
            cached = reusable_semantic_brief(indexed, semantic_cache)
            if not cached:
                continue
            section_id = canonical_section_id(
                f"{indexed.get('module', '')}.{indexed.get('category', '')}"
            )
            if section_id not in SECTION_ORDER_V13:
                continue
            target_section = provided_sections.setdefault(
                section_id, {"id": section_id, "briefs": [], "items": []}
            )
            target_section.setdefault("briefs", []).append(cached)
            existing_ids.add(item_id)
            source_counts[source_id] += 1
            warnings.append(f"reused approved semantic cache for brief {item_id!r}")
    fallback_ranks: dict[str, int] = {}
    item_ranks: dict[str, int] = {}
    for item in index.get("items", []):
        if not isinstance(item, dict) or not item.get("item_id"):
            continue
        source_id = str(item.get("source_id", ""))
        fallback_ranks[source_id] = fallback_ranks.get(source_id, 0) + 1
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        item_ranks[str(item["item_id"])] = int(
            metadata.get("source_rank")
            or metadata.get("hot_rank")
            or metadata.get("list_position")
            or fallback_ranks[source_id]
        )

    def authoritative_ref(item_id: str) -> dict[str, Any] | None:
        indexed = indexed_items.get(item_id)
        if not indexed:
            return None
        metadata = indexed.get("metadata", {})
        return {
            "item_id": item_id,
            "title": indexed.get("title", ""),
            "url": indexed.get("url", ""),
            "access": content_status_to_access(indexed.get("content_status"))
            or "metadata_only",
            "role": metadata.get("role", "discovery"),
            **(
                {"published_at": indexed["published_at"]}
                if indexed.get("published_at")
                else {}
            ),
        }

    for original_section_id, section in list(provided_sections.items()):
        retained_briefs: list[dict[str, Any]] = []
        for brief in section.get("briefs", []):
            if not isinstance(brief, dict):
                retained_briefs.append(brief)
                continue
            item_id = str(brief.get("item_id") or "")
            indexed = indexed_items.get(item_id)
            if not indexed:
                warnings.append(
                    f"omitted brief with unknown or missing item_id {item_id!r}; use an exact "
                    "item_id from context.brief_plan"
                )
                continue
            indexed_module = indexed.get("module")
            indexed_category = indexed.get("category")
            target_section_id = (
                canonical_section_id(f"{indexed_module}.{indexed_category}")
                if indexed_module and indexed_category
                else original_section_id
            )
            if target_section_id not in SECTION_ORDER_V13:
                warnings.append(
                    f"omitted brief {item_id!r} because its indexed section "
                    f"{target_section_id!r} is unsupported"
                )
                continue
            if target_section_id != original_section_id:
                target = provided_sections.setdefault(
                    target_section_id,
                    {"id": target_section_id, "briefs": [], "items": []},
                )
                target.setdefault("briefs", []).append(brief)
                warnings.append(
                    f"moved brief {item_id!r} from {original_section_id!r} to indexed section "
                    f"{target_section_id!r}"
                )
            else:
                retained_briefs.append(brief)
        section["briefs"] = retained_briefs

        retained_events: list[dict[str, Any]] = []
        for event in section.get("items", []):
            if not isinstance(event, dict):
                retained_events.append(event)
                continue
            refs = event.get("source_refs", [])
            source_item_ids = event.get("source_item_ids", [])
            primary_item_id = next(
                (
                    str(ref.get("item_id"))
                    for ref in refs
                    if isinstance(ref, dict) and ref.get("item_id")
                ),
                str(source_item_ids[0]) if source_item_ids else "",
            )
            indexed = indexed_items.get(primary_item_id)
            if not indexed:
                retained_events.append(event)
                continue
            indexed_module = indexed.get("module")
            indexed_category = indexed.get("category")
            target_section_id = (
                canonical_section_id(f"{indexed_module}.{indexed_category}")
                if indexed_module and indexed_category
                else original_section_id
            )
            if target_section_id in SECTION_ORDER_V13 and target_section_id != original_section_id:
                target = provided_sections.setdefault(
                    target_section_id,
                    {"id": target_section_id, "briefs": [], "items": []},
                )
                target.setdefault("items", []).append(event)
                warnings.append(
                    f"moved featured event for {primary_item_id!r} from "
                    f"{original_section_id!r} to indexed section {target_section_id!r}"
                )
            else:
                retained_events.append(event)
        section["items"] = retained_events

    compiled_sections: list[dict[str, Any]] = []
    event_by_item: dict[str, str] = {}
    used_brief_ids: set[str] = set()
    for section_id in SECTION_ORDER_V13:
        module, category = section_id.split(".", 1)
        section = provided_sections.get(section_id, {})
        section.update(
            {
                "id": section_id,
                "module": module,
                "category": category,
                "title": SECTION_TITLES_V13[section_id],
            }
        )
        section.setdefault("briefs", [])
        section.setdefault("items", [])
        deduplicated_briefs: list[dict[str, Any]] = []
        for brief in section["briefs"]:
            if not isinstance(brief, dict):
                deduplicated_briefs.append(brief)
                continue
            item_id = str(brief.get("item_id", ""))
            if item_id and item_id in used_brief_ids:
                warnings.append(f"omitted duplicate brief item_id {item_id!r}")
                continue
            deduplicated_briefs.append(brief)
            if item_id:
                used_brief_ids.add(item_id)
        section["briefs"] = deduplicated_briefs
        for brief in section["briefs"]:
            item_id = str(brief.get("item_id", ""))
            ref = authoritative_ref(item_id)
            if not ref:
                continue
            indexed = indexed_items[item_id]
            _normalize_brief_title(brief, indexed)
            source_id = str(indexed.get("source_id", ""))
            source = sources.get(source_id, {})
            brief["source_ref"] = ref
            brief["semantic_fingerprint"] = semantic_fingerprint(indexed)
            brief["primary_source"] = {
                "id": source_id,
                "name": indexed.get("source_name") or source.get("source_name") or source_id,
                "url": source.get("source_url") or indexed.get("url"),
            }
            rank = item_ranks.get(item_id, 1)
            brief["source_rank"] = rank
            brief["source_rank_label"] = _source_rank_label(source_id, rank)
            published = _publication_date(
                indexed.get("published_at"),
                str(index.get("timezone", "Asia/Shanghai")),
            )
            if brief.get("status") == "NEW":
                age = (
                    (date.fromisoformat(str(report["date"])) - published).days
                    if published
                    else None
                )
                if age not in {0, 1}:
                    brief["status"] = "WATCH"
        for event in section["items"]:
            refs = event.get("source_refs", [])
            source_item_ids = event.get("source_item_ids", [])
            requested_ids = [
                str(ref.get("item_id"))
                for ref in refs
                if isinstance(ref, dict) and ref.get("item_id")
            ] or [str(item_id) for item_id in source_item_ids]
            compiled_refs = [
                ref for item_id in requested_ids if (ref := authoritative_ref(item_id)) is not None
            ]
            if compiled_refs:
                event["source_refs"] = compiled_refs
                primary_item = indexed_items[compiled_refs[0]["item_id"]]
                primary_source_id = str(primary_item.get("source_id", ""))
                primary_source = sources.get(primary_source_id, {})
                event["primary_source"] = {
                    "id": primary_source_id,
                    "name": primary_item.get("source_name")
                    or primary_source.get("source_name")
                    or primary_source_id,
                    "url": primary_source.get("source_url") or primary_item.get("url"),
                }
            if not event.get("event_id") and compiled_refs:
                digest = hashlib.sha256(compiled_refs[0]["item_id"].encode()).hexdigest()[:8]
                event["event_id"] = f"EVT-{str(report['date']).replace('-', '')}-{digest.upper()}"
            event_id = str(event.get("event_id", ""))
            for ref in compiled_refs:
                event_by_item[ref["item_id"]] = event_id
            publication_dates = [
                value
                for ref in compiled_refs
                if (
                    value := _publication_date(
                        ref.get("published_at"),
                        str(index.get("timezone", "Asia/Shanghai")),
                    )
                )
            ]
            raw_newest_age = (
                (date.fromisoformat(str(report["date"])) - max(publication_dates)).days
                if publication_dates
                else 8
            )
            newest_age = max(0, raw_newest_age)
            importance, breakdown = _allocate_importance(
                int(event.get("importance", 50)), _freshness_cap(newest_age)
            )
            event["importance"] = importance
            event["importance_breakdown"] = breakdown
            event.setdefault(
                "importance_reason",
                "内部相对排序由生成 Agent 给出，分项由 Python 按约束归一化。",
            )
            event.setdefault("evidence_notes", [])
            event.setdefault("tags", [])
            access_levels = [ref["access"] for ref in compiled_refs]
            confidence = max(0.0, min(float(event.get("confidence", 0.6)), 1.0))
            if access_levels and all(
                access in {"metadata_only", "verification_required"} for access in access_levels
            ):
                confidence = min(confidence, 0.65)
                disclosure = " ".join(str(note) for note in event["evidence_notes"])
                markers = ("未读取正文", "公开摘要", "仅依据标题", "仅依据元数据")
                if not any(marker in disclosure for marker in markers):
                    event["evidence_notes"].append(
                        "未读取正文，仅依据索引标题或公开摘要；未补写不可见内容。"
                    )
            event["confidence"] = confidence
            if event.get("status") == "NEW" and raw_newest_age not in {0, 1}:
                event["status"] = "WATCH"
        section["items"].sort(key=lambda item: item.get("importance", 0), reverse=True)
        section["briefs"].sort(
            key=lambda item: (
                item.get("importance", 0),
                -int(item.get("source_rank", 1_000_000)),
            ),
            reverse=True,
        )
        if not section["items"] and not section["briefs"]:
            section.setdefault("coverage_note", "本时段未收集到可展示内容。")
        compiled_sections.append(section)
    report["sections"] = compiled_sections

    event_ids = {
        str(event.get("event_id"))
        for section in report["sections"]
        for event in section["items"]
        if event.get("event_id")
    }

    for position, analysis in enumerate(report.get("analyses", []), start=1):
        domain = str(analysis.get("domain", ""))
        analysis.setdefault(
            "analysis_id",
            f"ANALYSIS-{str(report['date']).replace('-', '')}-{domain or position}",
        )
        requirements = ANALYSIS_DOMAIN_REQUIREMENTS.get(domain, {})
        analysis["perspectives"] = list(
            dict.fromkeys(
                [
                    *requirements.get("perspectives", []),
                    *analysis.get("perspectives", []),
                ]
            )
        )
        analysis["assessment_types"] = list(
            dict.fromkeys(
                [*requirements.get("assessment_types", []), *analysis.get("assessment_types", [])]
            )
        )
        evidence_ids = analysis.get("evidence_item_ids") or analysis.get("evidence_event_ids", [])
        compiled_evidence: list[str] = []
        ignored_evidence: list[str] = []
        for item_id in evidence_ids:
            requested_id = str(item_id)
            event_id = event_by_item.get(requested_id)
            if event_id:
                compiled_evidence.append(event_id)
            elif requested_id in event_ids:
                compiled_evidence.append(requested_id)
            else:
                ignored_evidence.append(requested_id)
        analysis["evidence_event_ids"] = list(dict.fromkeys(compiled_evidence))
        if ignored_evidence:
            warnings.append(
                f"analysis {domain or position!r} ignored evidence item IDs that are not "
                f"part of any featured event: {sorted(set(ignored_evidence))}"
            )
    analysis_order = {"geopolitics": 0, "ai_technology": 1, "markets": 2}
    report.setdefault("analyses", []).sort(
        key=lambda analysis: analysis_order.get(str(analysis.get("domain")), 99)
    )

    briefs_by_item = {
        str(brief.get("item_id")): brief
        for section in report["sections"]
        for brief in section["briefs"]
        if brief.get("item_id")
    }
    for section in report["sections"]:
        for event in section["items"]:
            event_id = str(event.get("event_id", ""))
            for ref in event.get("source_refs", []):
                brief = briefs_by_item.get(str(ref.get("item_id")))
                if brief and event_id in event_ids:
                    brief["featured_event_id"] = event_id
                    break
    return warnings


def normalize_report_data(report: dict, index: dict | None) -> None:
    """Fill deterministic counters and source metrics for the current contract."""
    hydrate_report_evidence(report, index)
    if report.get("schema_version") != "1.5":
        return
    events = [item for section in report.get("sections", []) for item in section.get("items", [])]
    briefs = [item for section in report.get("sections", []) for item in section.get("briefs", [])]
    represented = {
        str(item.get("primary_source", {}).get("id"))
        for item in briefs
        if item.get("primary_source", {}).get("id")
    }
    report["event_count"] = len({item.get("event_id") for item in events})
    report["brief_count"] = len({item.get("item_id") for item in briefs})
    if isinstance(index, dict):
        sources = index.get("sources", [])
        successful = sum(
            row.get("status") in {"success", "partial"}
            for row in sources
            if isinstance(row, dict)
        )
        pending = sum(
            row.get("status")
            in {"failed", "verification_required", "rate_limited", "partial"}
            for row in sources
            if isinstance(row, dict)
        )
        report["source_count"] = len(represented)
        report["source_metrics"] = {
            "configured": len(sources),
            "successful": successful,
            "represented": len(represented),
            "pending": pending,
        }
        indexed_counts = Counter(
            str(item.get("source_id"))
            for item in index.get("items", [])
            if isinstance(item, dict) and item.get("source_id")
        )
        brief_counts = Counter(
            str(item.get("primary_source", {}).get("id"))
            for item in briefs
            if item.get("primary_source", {}).get("id")
        )
        policies = index.get("source_policies", {})
        report["coverage_metrics"] = [
            {
                "source_id": source_id,
                "available": indexed_counts[source_id],
                "selected": brief_counts[source_id],
                "target": min(
                    indexed_counts[source_id],
                    int(policy.get("report_target", 10)),
                ),
                "maximum": int(policy.get("report_max", 15)),
            }
            for source_id, policy in policies.items()
            if indexed_counts[source_id]
        ]
    report.setdefault("evaluation_status", "pending")


def report_content_hash(report: dict) -> str:
    payload = {key: value for key, value in report.items() if key != "quality_evaluation"}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_report_data(
    report: object,
    index: object | None = None,
    existing_events: list[dict] | None = None,
) -> tuple[list[str], list[str]]:
    if isinstance(report, dict):
        normalize_report_data(report, index if isinstance(index, dict) else None)
    errors = _schema_errors(report)
    if errors or not isinstance(report, dict):
        return errors or ["Report must be a JSON object"], []

    warnings: list[str] = []
    schema_version = report.get("schema_version")
    strict_contract = schema_version in {"1.1", "1.2", "1.3", "1.4", "1.5"}
    freshness_contract = schema_version in {"1.2", "1.3", "1.4", "1.5"}
    daily_contract = schema_version in {"1.3", "1.4", "1.5"}
    source_group_contract = schema_version in {"1.4", "1.5"}
    brief_contract = schema_version == "1.5"

    def require_chinese(value: object, location: str) -> None:
        if strict_contract:
            _require_chinese(value, location, errors)

    if strict_contract and report.get("language") != "zh-CN":
        errors.append(f"language: schema_version {schema_version} requires 'zh-CN'")
    require_chinese(report.get("title"), "title")
    for summary_index, summary in enumerate(report.get("executive_summary", [])):
        require_chinese(summary, f"executive_summary[{summary_index}]")
    known_items: dict[str, dict] = {}
    source_aliases: dict[str, set[str]] = {}
    if isinstance(index, dict):
        index_items = index.get("items", [])
        if isinstance(index_items, list):
            known_items = {
                item.get("item_id"): item
                for item in index_items
                if isinstance(item, dict) and item.get("item_id")
            }
        for source in index.get("sources", []):
            if not isinstance(source, dict) or not source.get("source_id"):
                continue
            source_id = str(source["source_id"])
            name = str(source.get("source_name") or "").strip()
            if len(name) >= 3:
                source_aliases.setdefault(source_id, set()).add(name.casefold())
        for item in known_items.values():
            source_id = str(item.get("source_id") or "")
            name = str(item.get("source_name") or "").strip()
            if source_id and len(name) >= 3:
                source_aliases.setdefault(source_id, set()).add(name.casefold())

    def mentioned_source_ids(values: list[object]) -> set[str]:
        text = " ".join(str(value) for value in values).casefold()
        return {
            source_id
            for source_id, aliases in source_aliases.items()
            if any(alias in text for alias in aliases)
        }
    previous_events = {
        event.get("event_id"): event
        for event in (existing_events or [])
        if isinstance(event, dict) and event.get("event_id")
    }
    previous_item_ids = {
        item_id
        for event in previous_events.values()
        for item_id in event.get("source_item_ids", [])
        if isinstance(item_id, str)
    }
    report_date = date.fromisoformat(report["date"])
    timezone = (
        str(index.get("timezone", "Asia/Shanghai"))
        if isinstance(index, dict)
        else "Asia/Shanghai"
    )

    event_ids: set[str] = set()
    event_source_ids: dict[str, set[str]] = {}
    featured_brief_event_ids: set[str] = set()
    brief_item_ids: set[str] = set()
    source_counts: Counter[str] = Counter()
    source_importance_values: dict[str, list[int]] = {}
    section_ids: set[str] = set()
    for section_index, section in enumerate(report["sections"]):
        prefix = f"sections[{section_index}]"
        try:
            validate_content_taxonomy(section["module"], section["category"])
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        expected_id = f"{section['module']}.{section['category']}"
        if section["id"] != expected_id:
            errors.append(f"{prefix}: id must be {expected_id!r}")
        if section["id"] in section_ids:
            errors.append(f"{prefix}: duplicate section id {section['id']!r}")
        section_ids.add(section["id"])
        require_chinese(section.get("title"), f"{prefix}.title")
        if daily_contract and section.get("title") != SECTION_TITLES_V13.get(section["id"]):
            errors.append(
                f"{prefix}.title: schema {schema_version} requires "
                f"{SECTION_TITLES_V13.get(section['id'])!r} for {section['id']!r}"
            )
        briefs = section.get("briefs", []) if brief_contract else []
        if brief_contract and "briefs" not in section:
            errors.append(f"{prefix}.briefs: required by schema_version 1.5")
        if strict_contract and not section["items"] and not briefs:
            note = section.get("coverage_note")
            if not note:
                errors.append(f"{prefix}: empty section requires coverage_note")
            else:
                require_chinese(note, f"{prefix}.coverage_note")

        importance_values = [item["importance"] for item in section["items"]]
        if importance_values != sorted(importance_values, reverse=True):
            errors.append(f"{prefix}: items must be sorted by importance descending")

        brief_importance_values = [item["importance"] for item in briefs]
        if brief_importance_values != sorted(brief_importance_values, reverse=True):
            errors.append(f"{prefix}: briefs must be sorted by importance descending")

        for brief_index, brief in enumerate(briefs):
            brief_prefix = f"{prefix}.briefs[{brief_index}]"
            item_id = brief["item_id"]
            if item_id in brief_item_ids:
                errors.append(f"{brief_prefix}: duplicate item_id {item_id}")
            brief_item_ids.add(item_id)
            require_chinese(brief.get("tldr"), f"{brief_prefix}.tldr")
            if issue := _tldr_quality_issue(brief.get("tldr"), str(brief.get("title", ""))):
                errors.append(f"{brief_prefix}.tldr: {issue}; item_id={item_id}")
            primary = brief.get("primary_source", {})
            source_id = str(primary.get("id", ""))
            source_counts[source_id] += 1
            if source_id:
                source_importance_values.setdefault(source_id, []).append(brief["importance"])
            if urlsplit(str(primary.get("url", ""))).scheme not in {"http", "https"}:
                errors.append(f"{brief_prefix}.primary_source.url: invalid URL")
            ref = brief.get("source_ref", {})
            if ref.get("item_id") != item_id:
                errors.append(f"{brief_prefix}.source_ref.item_id must equal brief item_id")
            if known_items and item_id not in known_items:
                errors.append(f"{brief_prefix}: item_id not present in index: {item_id}")
                continue
            indexed = known_items.get(item_id, {})
            indexed_url = str(indexed.get("url", ""))
            if indexed_url and canonicalize_url(str(ref.get("url", ""))) != canonicalize_url(
                indexed_url
            ):
                errors.append(f"{brief_prefix}.source_ref.url does not match indexed item URL")
            if indexed.get("title") and ref.get("title") != indexed.get("title"):
                errors.append(f"{brief_prefix}.source_ref.title does not match indexed item title")
            indexed_title = str(indexed.get("title") or "")
            if indexed_title and brief.get("title") != indexed_title:
                errors.append(f"{brief_prefix}.title must preserve the indexed original headline")
            title_zh = str(brief.get("title_zh") or "").strip()
            if indexed_title and not _CJK_PATTERN.search(indexed_title):
                if not title_zh or not _CJK_PATTERN.search(title_zh):
                    errors.append(
                        f"{brief_prefix}.title_zh: non-Chinese headline requires a Chinese "
                        f"translation on the following line; item_id={item_id}"
                    )
                elif _LANGUAGE_MARKER_PATTERN.match(title_zh):
                    errors.append(f"{brief_prefix}.title_zh: remove the [英]/[EN] marker")
            elif indexed_title and title_zh:
                errors.append(
                    f"{brief_prefix}.title_zh: omit the redundant translation for a "
                    "Chinese headline"
                )
            if indexed.get("source_id") and source_id != str(indexed.get("source_id", "")):
                errors.append(f"{brief_prefix}.primary_source.id does not match indexed source")
            actual_access = content_status_to_access(indexed.get("content_status"))
            if actual_access and ref.get("access") != actual_access:
                errors.append(
                    f"{brief_prefix}.source_ref.access {ref.get('access')!r} does not match "
                    f"index content_status {indexed.get('content_status')!r}"
                )
            if actual_access in {"full_text", "partial"} and any(
                marker in str(brief.get("tldr", "")) for marker in _UNREAD_BODY_MARKERS
            ):
                errors.append(
                    f"{brief_prefix}.tldr: index contains {actual_access} content; do not claim "
                    f"that the body was unread; item_id={item_id}"
                )
            published_date = _publication_date(
                indexed.get("published_at") or ref.get("published_at"), timezone
            )
            if brief["status"] == "NEW":
                if published_date is None:
                    errors.append(
                        f"{brief_prefix}.status: NEW requires a parseable indexed publication date"
                    )
                elif (report_date - published_date).days not in {0, 1}:
                    errors.append(
                        f"{brief_prefix}.status: NEW requires publication today or yesterday"
                    )
            if brief.get("featured_event_id"):
                featured_brief_event_ids.add(str(brief["featured_event_id"]))

        for item_index, item in enumerate(section["items"]):
            item_prefix = f"{prefix}.items[{item_index}]"
            event_id = item["event_id"]
            if event_id in event_ids:
                errors.append(f"{item_prefix}: duplicate event_id {event_id}")
            event_ids.add(event_id)
            for field in ("title", "tldr", "why_it_matters", "importance_reason"):
                require_chinese(item.get(field), f"{item_prefix}.{field}")
            if _LANGUAGE_MARKER_PATTERN.match(str(item.get("title", ""))):
                errors.append(f"{item_prefix}.title: remove the [英]/[EN] marker")
            if issue := _tldr_quality_issue(item.get("tldr"), str(item.get("title", ""))):
                errors.append(f"{item_prefix}.tldr: {issue}")
            if source_group_contract:
                primary = item.get("primary_source")
                if not isinstance(primary, dict):
                    errors.append(
                        f"{item_prefix}.primary_source: required by schema_version {schema_version}"
                    )
                else:
                    source_id = str(primary.get("id", ""))
                    if not brief_contract:
                        source_counts[source_id] += 1
                    if urlsplit(str(primary.get("url", ""))).scheme not in {"http", "https"}:
                        errors.append(f"{item_prefix}.primary_source.url: invalid URL")
                image = item.get("image")
                if isinstance(image, dict):
                    require_chinese(image.get("caption"), f"{item_prefix}.image.caption")
            for note_index, note in enumerate(item.get("evidence_notes", [])):
                require_chinese(note, f"{item_prefix}.evidence_notes[{note_index}]")

            score = item["importance_breakdown"]
            calculated = sum(score.values())
            if calculated != item["importance"]:
                errors.append(
                    f"{item_prefix}: importance {item['importance']} "
                    f"does not equal breakdown total {calculated}"
                )

            publication_dates: list[date] = []
            referenced_item_ids: set[str] = set()
            referenced_source_ids: list[str] = []
            access_levels: list[str] = []
            for ref_index, ref in enumerate(item["source_refs"]):
                ref_prefix = f"{item_prefix}.source_refs[{ref_index}]"
                if urlsplit(ref["url"]).scheme not in {"http", "https"}:
                    errors.append(f"{ref_prefix}: invalid URL")
                item_id = ref["item_id"]
                referenced_item_ids.add(item_id)
                access_levels.append(ref["access"])
                if known_items and item_id not in known_items:
                    errors.append(f"{ref_prefix}: item_id not present in index: {item_id}")
                if item_id in known_items:
                    indexed = known_items[item_id]
                    if indexed.get("source_id"):
                        referenced_source_ids.append(str(indexed["source_id"]))
                    content_status = known_items[item_id].get("content_status")
                    actual_access = content_status_to_access(content_status)
                    if actual_access and ref["access"] != actual_access:
                        errors.append(
                            f"{ref_prefix}: access {ref['access']!r} does not match "
                            f"index content_status {content_status!r}, which maps to "
                            f"report access {actual_access!r}"
                        )
                    if indexed.get("url") and canonicalize_url(ref["url"]) != canonicalize_url(
                        str(indexed["url"])
                    ):
                        errors.append(f"{ref_prefix}: URL does not match indexed item URL")
                    if indexed.get("title") and ref.get("title") != indexed.get("title"):
                        errors.append(f"{ref_prefix}: title does not match indexed item title")
                    primary = item.get("primary_source", {})
                    if (
                        ref_index == 0
                        and indexed.get("source_id")
                        and primary.get("id") != indexed.get("source_id")
                    ):
                        errors.append(
                            f"{item_prefix}.primary_source.id does not match the first "
                            "indexed source reference"
                        )
                    published_at = known_items[item_id].get("published_at")
                    published_date = _publication_date(published_at, timezone)
                    if published_date is not None:
                        publication_dates.append(published_date)
                elif ref.get("published_at"):
                    published_date = _publication_date(ref.get("published_at"), timezone)
                    if published_date is not None:
                        publication_dates.append(published_date)

            repeated_sources = sorted(
                source_id
                for source_id, count in Counter(referenced_source_ids).items()
                if count > 1
            )
            event_source_ids[event_id] = set(referenced_source_ids)
            note_mentions = mentioned_source_ids(list(item.get("evidence_notes", [])))
            unbound_note_sources = sorted(note_mentions - event_source_ids[event_id])
            if unbound_note_sources:
                errors.append(
                    f"{item_prefix}.evidence_notes names sources not bound in source_refs: "
                    f"{unbound_note_sources}. Add separate featured events and cite them from "
                    "analysis instead of claiming unbound corroboration."
                )
            if brief_contract and len(item["source_refs"]) > 1:
                errors.append(
                    f"{item_prefix}.source_refs: schema 1.5 featured events require exactly one "
                    "source item. Keep corroborating articles as separate featured events and "
                    "cite both event IDs in analysis"
                )
            elif repeated_sources:
                errors.append(
                    f"{item_prefix}.source_refs: a featured event may use at most one item per "
                    f"publisher; repeated sources {repeated_sources}. Keep one primary article and "
                    "use distinct publishers only when they corroborate the same event"
                )

            if freshness_contract and access_levels and all(
                access in {"metadata_only", "verification_required"} for access in access_levels
            ) and item["confidence"] > 0.65:
                errors.append(
                    f"{item_prefix}: confidence above 0.65 requires at least one partial or "
                    "full-text source"
                )

            if daily_contract and access_levels and all(
                access in {"metadata_only", "verification_required"} for access in access_levels
            ):
                disclosure = " ".join(str(note) for note in item.get("evidence_notes", []))
                markers = ("未读取正文", "公开摘要", "仅依据标题", "仅依据元数据")
                if not any(marker in disclosure for marker in markers):
                    errors.append(
                        f"{item_prefix}.evidence_notes: metadata-only evidence must disclose "
                        "that the body was not read or that only a public abstract/title was used"
                    )

            if freshness_contract and publication_dates:
                raw_newest_age = (report_date - max(publication_dates)).days
                newest_age = max(0, raw_newest_age)
                cap = _freshness_cap(newest_age)
                if score["freshness"] > cap:
                    errors.append(
                        f"{item_prefix}.importance_breakdown.freshness: {score['freshness']} "
                        f"exceeds age-based cap {cap}; newest source is {newest_age} day(s) old"
                    )
                if item["status"] == "NEW" and raw_newest_age not in {0, 1}:
                    errors.append(
                        f"{item_prefix}.status: NEW requires source evidence published today "
                        "or yesterday; use WATCH/UPD or provide newer evidence"
                    )
            elif freshness_contract and item["status"] == "NEW":
                message = f"{item_prefix}.status: NEW has no parseable source publication date"
                (errors if brief_contract else warnings).append(message)

            if freshness_contract and item["status"] == "NEW":
                if event_id in previous_events:
                    errors.append(
                        f"{item_prefix}.status: event_id {event_id!r} already exists; "
                        "reuse it with UPD/CONF/REV/WATCH/CLOSED"
                    )
                reused_items = sorted(referenced_item_ids & previous_item_ids)
                if reused_items:
                    errors.append(
                        f"{item_prefix}.status: NEW reuses previously reported source items "
                        f"{reused_items}; split continuing evidence from genuinely new events"
                    )

    missing_sections = sorted(required_section_ids(schema_version) - section_ids)
    if strict_contract and missing_sections:
        errors.append(f"sections missing required ids: {missing_sections}")
    if daily_contract:
        unexpected_sections = sorted(section_ids - required_section_ids(schema_version))
        if unexpected_sections:
            errors.append(
                f"sections contain unsupported ids for schema {schema_version}: "
                f"{unexpected_sections}"
            )
    if source_group_contract:
        over_limit = {source_id: count for source_id, count in source_counts.items() if count > 15}
        if over_limit:
            errors.append(f"primary sources exceed 15 selected items: {over_limit}")
    if brief_contract:
        for source_id, values in sorted(source_importance_values.items()):
            if len(values) >= 3 and len(set(values)) == 1:
                warnings.append(
                    f"source {source_id!r} has {len(values)} briefs with identical importance; "
                    "source_rank is used as the deterministic tie-breaker"
                )
        if isinstance(index, dict):
            indexed_counts = Counter(
                str(item.get("source_id"))
                for item in index.get("items", [])
                if isinstance(item, dict) and item.get("source_id")
            )
            policies = (
                index.get("source_policies", {})
                if isinstance(index.get("source_policies"), dict)
                else {}
            )
            for source_id, policy in sorted(policies.items()):
                if not isinstance(policy, dict) or not indexed_counts[source_id]:
                    continue
                target = min(
                    indexed_counts[source_id],
                    int(policy.get("report_target", 10)),
                    int(policy.get("report_max", 15)),
                    15,
                )
                selected = source_counts[source_id]
                if selected < target:
                    errors.append(
                        f"coverage source {source_id!r}: selected {selected} of required {target}; "
                        "complete this source's entries from context.brief_plan before finalization"
                    )

    if brief_contract:
        if len(event_ids) > MAX_FEATURED_EVENTS:
            errors.append(
                f"schema 1.5 allows at most {MAX_FEATURED_EVENTS} featured events; "
                "keep the remaining stories as briefs"
            )
        missing_featured = sorted(event_ids - featured_brief_event_ids)
        unknown_featured = sorted(featured_brief_event_ids - event_ids)
        if missing_featured:
            errors.append(f"briefs missing featured_event_id links for events: {missing_featured}")
        if unknown_featured:
            errors.append(f"briefs reference unknown featured events: {unknown_featured}")

    if report["event_count"] != len(event_ids):
        errors.append(
            f"event_count is {report['event_count']}, "
            f"but {len(event_ids)} unique event IDs were found"
        )

    if daily_contract and not report["analyses"]:
        errors.append(
            f"analyses: schema_version {schema_version} requires evidence-backed judgement"
        )

    analysis_ids: set[str] = set()
    assessment_types: set[str] = set()
    perspectives: set[str] = set()
    analyzed_event_ids: set[str] = set()
    for analysis_index, analysis in enumerate(report["analyses"]):
        analysis_prefix = f"analyses[{analysis_index}]"
        require_chinese(analysis.get("claim"), f"{analysis_prefix}.claim")
        require_chinese(analysis.get("reasoning"), f"{analysis_prefix}.reasoning")
        if source_group_contract:
            for field in ("narrative", "dialectical_analysis", "historical_context"):
                require_chinese(analysis.get(field), f"{analysis_prefix}.{field}")
                if not analysis.get(field):
                    errors.append(
                        f"{analysis_prefix}.{field}: required by schema_version {schema_version}"
                    )
            perspectives.update(analysis.get("perspectives", []))
            if not analysis.get("perspectives"):
                errors.append(
                    f"{analysis_prefix}.perspectives: required by schema_version {schema_version}"
                )
            positions = analysis.get("stakeholder_positions", [])
            if not positions:
                errors.append(
                    f"{analysis_prefix}.stakeholder_positions: required by schema_version "
                    f"{schema_version}"
                )
            for position_index, position in enumerate(positions):
                for field in ("stakeholder", "position", "interests"):
                    require_chinese(
                        position.get(field),
                        f"{analysis_prefix}.stakeholder_positions[{position_index}].{field}",
                    )
        for field in (
            "facts",
            "counter_evidence",
            "scenarios",
            "implications",
            "actions",
            "watch_signals",
            "invalidation_signals",
        ):
            for value_index, value in enumerate(analysis.get(field, [])):
                require_chinese(value, f"{analysis_prefix}.{field}[{value_index}]")
        if strict_contract:
            for field in ("facts", "reasoning", "scenarios", "actions", "invalidation_signals"):
                if not analysis.get(field):
                    errors.append(
                        f"{analysis_prefix}.{field}: required by schema_version {schema_version}"
                    )
        if daily_contract:
            assessment_types.update(analysis.get("assessment_types", []))
            if not analysis.get("assessment_types"):
                errors.append(
                    f"{analysis_prefix}.assessment_types: required by schema_version "
                    f"{schema_version}"
                )
            if not analysis.get("evidence_event_ids"):
                errors.append(
                    f"{analysis_prefix}.evidence_event_ids: judgement must cite report events"
                )
        if analysis["analysis_id"] in analysis_ids:
            errors.append(
                f"analyses[{analysis_index}]: duplicate analysis_id {analysis['analysis_id']}"
            )
        analysis_ids.add(analysis["analysis_id"])
        missing_events = [
            event_id for event_id in analysis["evidence_event_ids"] if event_id not in event_ids
        ]
        if missing_events:
            errors.append(
                f"analyses[{analysis_index}] references unknown event IDs: {missing_events}"
            )
        bound_analysis_sources = {
            source_id
            for event_id in analysis.get("evidence_event_ids", [])
            for source_id in event_source_ids.get(str(event_id), set())
        }
        fact_mentions = mentioned_source_ids(list(analysis.get("facts", [])))
        unbound_fact_sources = sorted(fact_mentions - bound_analysis_sources)
        if unbound_fact_sources:
            errors.append(
                f"{analysis_prefix}.facts names sources not represented by evidence_event_ids: "
                f"{unbound_fact_sources}"
            )
        numeric_scenarios = [
            value
            for value in analysis.get("scenarios", [])
            if _NUMERIC_SCENARIO_PATTERN.search(str(value))
        ]
        if numeric_scenarios:
            scenario_basis = str(analysis.get("scenario_basis") or "").strip()
            if not scenario_basis:
                errors.append(
                    f"{analysis_prefix}.scenario_basis: numeric probabilities, prices, or ranges "
                    "must state their source or explicitly identify them as scenario assumptions"
                )
            else:
                require_chinese(scenario_basis, f"{analysis_prefix}.scenario_basis")
        analyzed_event_ids.update(analysis["evidence_event_ids"])
        if not analysis["watch_signals"]:
            warnings.append(f"analyses[{analysis_index}] has no watch_signals")
    if daily_contract:
        missing_assessments = sorted(
            {"trend", "risk", "learning_research"} - assessment_types
        )
        if missing_assessments:
            errors.append(
                "analyses missing required assessment coverage: " f"{missing_assessments}"
            )
    if source_group_contract:
        missing_perspectives = sorted(REQUIRED_PERSPECTIVES - perspectives)
        if missing_perspectives:
            errors.append(f"analyses missing required perspectives: {missing_perspectives}")
        if event_ids and len(analyzed_event_ids) / len(event_ids) < 0.6:
            errors.append(
                "analyses must cite at least 60% of selected events; "
                f"covered {len(analyzed_event_ids)}/{len(event_ids)}"
            )
    if brief_contract:
        analysis_domains = Counter(
            str(analysis.get("domain")) for analysis in report.get("analyses", [])
        )
        missing_domains = sorted(set(ANALYSIS_DOMAIN_REQUIREMENTS) - set(analysis_domains))
        if missing_domains:
            errors.append(
                "analyses must contain separate geopolitics, ai_technology, and markets "
                f"sections; missing {missing_domains}"
            )
    for change_index, change in enumerate(report.get("changes", [])):
        require_chinese(change, f"changes[{change_index}]")
    for watch_index, watch in enumerate(report.get("tomorrow_watch_items", [])):
        require_chinese(watch, f"tomorrow_watch_items[{watch_index}]")
    if daily_contract and report["edition"] == "evening":
        if not report.get("changes"):
            errors.append(
                f"changes: evening schema {schema_version} report must state additions, "
                "confirmations, "
                "corrections, or explicitly state that no material change occurred"
            )
        if not report.get("tomorrow_watch_items"):
            errors.append(
                f"tomorrow_watch_items: evening schema {schema_version} report requires "
                "next-day watch items"
            )
    for pending_index, pending in enumerate(report.get("pending_verifications", [])):
        require_chinese(pending.get("note"), f"pending_verifications[{pending_index}].note")
        if source_group_contract and urlsplit(str(pending.get("url", ""))).scheme not in {
            "http",
            "https",
        }:
            errors.append(f"pending_verifications[{pending_index}].url: required valid link")

    if schema_version == "1.4":
        evaluation = report.get("quality_evaluation")
        if not isinstance(evaluation, dict):
            errors.append("quality_evaluation: schema_version 1.4 requires independent evaluation")
        else:
            dimensions = evaluation.get("dimensions", [])
            dimension_ids = [item.get("id") for item in dimensions if isinstance(item, dict)]
            if set(dimension_ids) != EVALUATION_DIMENSIONS or len(dimension_ids) != 9:
                errors.append("quality_evaluation.dimensions: require all nine unique dimensions")
            scores = [
                item.get("score")
                for item in dimensions
                if isinstance(item, dict) and isinstance(item.get("score"), int)
            ]
            if len(scores) == 9 and evaluation.get("total_score") != sum(scores):
                errors.append("quality_evaluation.total_score: must equal dimension score sum")
            for dimension_index, dimension in enumerate(dimensions):
                require_chinese(
                    dimension.get("finding"),
                    f"quality_evaluation.dimensions[{dimension_index}].finding",
                )
            for field in ("main_defects", "insufficient_evidence", "improvements"):
                for value_index, value in enumerate(evaluation.get(field, [])):
                    require_chinese(value, f"quality_evaluation.{field}[{value_index}]")
            if evaluation.get("evaluated_report_id") != report.get("report_id"):
                errors.append("quality_evaluation.evaluated_report_id must match report_id")
            if (
                evaluation.get("continuity_decision") == "reject"
                and "all" not in evaluation.get("exclude_from_continuity", [])
            ):
                errors.append(
                    "quality_evaluation.exclude_from_continuity: reject requires 'all'"
                )
    if brief_contract and report.get("evaluation_status") != "pending":
        errors.append("evaluation_status: newly published schema 1.5 report must be pending")
    if brief_contract and "quality_evaluation" in report:
        errors.append(
            "quality_evaluation: schema 1.5 stores evaluation as a separate "
            "post-publication artifact"
        )
    return errors, warnings


def validate_report(
    report_path: Path,
    index_path: Path | None = None,
    events_path: Path | None = None,
) -> tuple[list[str], list[str]]:
    report = read_json(report_path)
    index = read_json(index_path) if index_path else None
    compile_warnings: list[str] = []
    if isinstance(report, dict) and isinstance(index, dict):
        report = deepcopy(report)
        try:
            compile_warnings = compile_report_data(report, index)
        except ValueError as exc:
            return [f"Report draft compilation failed: {exc}"], []
    existing_events: list[dict] = []
    if events_path and events_path.exists():
        payload = read_json(events_path)
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            existing_events = [item for item in payload["items"] if isinstance(item, dict)]
    errors, validation_warnings = validate_report_data(report, index, existing_events)
    return errors, [*compile_warnings, *validation_warnings]


def validate_evaluation_data(evaluation: object, report: object) -> list[str]:
    if not isinstance(evaluation, dict) or not isinstance(report, dict):
        return ["Evaluation and report must both be JSON objects"]
    errors: list[str] = []
    if evaluation.get("evaluator_role") != "independent":
        errors.append("evaluator_role must be 'independent'")
    if evaluation.get("evaluated_report_id") != report.get("report_id"):
        errors.append("evaluated_report_id must match the immutable report")
    expected_hash = report_content_hash(report)
    if evaluation.get("evaluated_content_hash") != expected_hash:
        errors.append("evaluated_content_hash does not match the immutable report content")
    dimensions = evaluation.get("dimensions", [])
    dimension_ids = [item.get("id") for item in dimensions if isinstance(item, dict)]
    if set(dimension_ids) != EVALUATION_DIMENSIONS or len(dimension_ids) != 9:
        errors.append("dimensions must contain all nine unique evaluation dimensions")
    scores = [
        item.get("score")
        for item in dimensions
        if isinstance(item, dict) and isinstance(item.get("score"), int)
    ]
    if len(scores) != 9 or any(score < 1 or score > 5 for score in scores):
        errors.append("each evaluation dimension score must be an integer from 1 to 5")
    elif evaluation.get("total_score") != sum(scores):
        errors.append("total_score must equal the sum of all dimension scores")
    for position, dimension in enumerate(dimensions):
        _require_chinese(dimension.get("finding"), f"dimensions[{position}].finding", errors)
    for field in ("main_defects", "insufficient_evidence", "improvements"):
        values = evaluation.get(field)
        if not isinstance(values, list):
            errors.append(f"{field} must be an array")
            continue
        for position, value in enumerate(values):
            _require_chinese(value, f"{field}[{position}]", errors)
    if evaluation.get("continuity_decision") not in {"accept", "selective", "reject"}:
        errors.append("continuity_decision must be accept, selective, or reject")
    excluded = evaluation.get("exclude_from_continuity", [])
    allowed = {"formatting", "event_summaries", "analyses", "source_access", "all"}
    if not isinstance(excluded, list) or not set(excluded) <= allowed:
        errors.append("exclude_from_continuity contains unsupported values")
    if evaluation.get("continuity_decision") == "reject" and "all" not in excluded:
        errors.append("reject continuity_decision requires exclude_from_continuity=['all']")
    effective_decision, effective_excluded, continuity_error = (
        evaluation_continuity_floor(evaluation)
    )
    if continuity_error and (
        effective_decision != evaluation.get("continuity_decision")
        or effective_excluded != set(excluded)
    ):
        errors.append(continuity_error)
    return errors
