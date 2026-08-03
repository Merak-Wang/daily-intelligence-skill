from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from .localization import validate_output_language
from .utils import canonicalize_url, clean_title, now_iso, read_json, write_json

SEMANTIC_CACHE_SCHEMA = "1.1"
REUSABLE_BRIEF_FIELDS = (
    "item_id",
    "title",
    "title_zh",
    "title_en",
    "tldr",
    "importance",
    "status",
)


def semantic_fingerprint(item: dict[str, Any]) -> str:
    """处理：只哈希会合理改变翻译或摘要语义的证据字段。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    输出：“只哈希会合理改变翻译或摘要语义的证据字段”得到的规范字符串，供调用方存储、比较或展示。
    """
    payload = {
        "title": clean_title(str(item.get("title") or "")),
        "url": canonicalize_url(str(item.get("canonical_url") or item.get("url") or "")),
        "description": clean_title(str(item.get("description") or "")),
        "published_at": str(item.get("published_at") or ""),
        "content_status": str(item.get("content_status") or "not_fetched"),
        "content_path": str(item.get("content_path") or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def semantic_cache_path(data_dir: Path) -> Path:
    """处理：返回按语言隔离的语义简报缓存文件路径。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“返回按语言隔离的语义简报缓存文件路径”所生成、定位或确认产物的本地路径。
    """
    return data_dir / "state" / "semantic-cache.json"


def load_semantic_cache(data_dir: Path) -> dict[str, dict[str, Any]]:
    """处理：读取指定语言的语义简报缓存，文件缺失或损坏时返回空缓存。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：“读取指定语言的语义简报缓存，文件缺失或损坏时返回空缓存”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    path = semantic_cache_path(data_dir)
    if not path.exists():
        return {}
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        raise ValueError(f"Invalid semantic cache: {path}")
    return {
        str(item_id): entry
        for item_id, entry in payload["items"].items()
        if isinstance(entry, dict)
    }


def reusable_semantic_brief(
    item: dict[str, Any],
    cache: dict[str, dict[str, Any]],
    output_language: str = "zh-CN",
) -> dict[str, Any] | None:
    """处理：校验指纹、语言和审核状态后返回可复用简报。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``cache``：本地持久化缓存对象；包含状态、时间、响应元数据和可复用结果。
    - ``output_language``：目标报告语言；决定标题译文字段、校验规则和界面文本。
    输出：“校验指纹、语言和审核状态后返回可复用简报”形成的结构化字典；
      键值表达该处理定义的业务记录或查找关系。
    """
    language = validate_output_language(output_language)
    item_id = str(item.get("item_id") or "")
    entry = cache.get(item_id)
    if (
        not entry
        or entry.get("state") != "approved"
        or entry.get("fingerprint") != semantic_fingerprint(item)
        or str(entry.get("language") or "zh-CN") != language
    ):
        return None
    brief = entry.get("brief")
    if not isinstance(brief, dict):
        return None
    reused = {
        key: deepcopy(brief[key])
        for key in REUSABLE_BRIEF_FIELDS
        if key in brief
    }
    if not reused.get("tldr") or not reused.get("importance"):
        return None
    reused["semantic_fingerprint"] = entry["fingerprint"]
    reused["semantic_cache_source_report_id"] = entry.get("report_id")
    reused["semantic_cache_reused"] = True
    return reused


def update_semantic_cache_from_report(
    report: dict[str, Any], index: dict[str, Any], data_dir: Path
) -> Path:
    """处理：从已验证报告更新按条目指纹索引的语义缓存。
    输入：
    - ``report``：当前报告结构；包含栏目、简报或事件、来源引用及质量元数据。
    - ``index``：当前来源索引对象；包含规范条目、来源结果、策略和采集时间。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“从已验证报告更新按条目指纹索引的语义缓存”所生成、定位或确认产物的本地路径。
    """
    path = semantic_cache_path(data_dir)
    cache = load_semantic_cache(data_dir)
    indexed_items = {
        str(item.get("item_id")): item
        for item in index.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    report_id = str(report.get("report_id") or "")
    output_language = validate_output_language(
        str(report.get("language") or "zh-CN")
    )
    updated_at = str(report.get("generated_at") or now_iso("Asia/Shanghai"))
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        for brief in section.get("briefs", []):
            if not isinstance(brief, dict):
                continue
            item_id = str(brief.get("item_id") or "")
            indexed = indexed_items.get(item_id)
            if not indexed:
                continue
            fingerprint = semantic_fingerprint(indexed)
            existing = cache.get(item_id)
            current_brief = {
                key: deepcopy(brief[key])
                for key in REUSABLE_BRIEF_FIELDS
                if key in brief
            }
            if (
                existing
                and existing.get("state") == "approved"
                and existing.get("fingerprint") == fingerprint
                and str(existing.get("language") or "zh-CN") == output_language
                and existing.get("brief") == current_brief
            ):
                continue
            cache[item_id] = {
                "fingerprint": fingerprint,
                "language": output_language,
                "state": "pending",
                "report_id": report_id,
                "updated_at": updated_at,
                "brief": current_brief,
            }
    return write_json(
        path,
        {"schema_version": SEMANTIC_CACHE_SCHEMA, "items": cache},
    )


def finalize_semantic_cache_evaluation(
    evaluation: dict[str, Any], data_dir: Path
) -> Path | None:
    """处理：完成并固化语义缓存评估。
    输入：
    - ``evaluation``：独立质量评估对象；包含评分、问题和改进建议。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    输出：指向“完成并固化语义缓存评估”所生成、定位或确认产物的本地路径；条件不满足时返回 None。
    """
    path = semantic_cache_path(data_dir)
    if not path.exists():
        return None
    cache = load_semantic_cache(data_dir)
    report_id = str(evaluation.get("evaluated_report_id") or "")
    dimensions = {
        str(row.get("id")): int(row.get("score", 0))
        for row in evaluation.get("dimensions", [])
        if isinstance(row, dict) and row.get("id")
    }
    continuity = str(evaluation.get("continuity_decision") or "selective")
    excluded = {
        str(value) for value in evaluation.get("exclude_from_continuity", [])
    }
    approved = (
        continuity != "reject"
        and not excluded.intersection({"all", "event_summaries"})
        and dimensions.get("summary_accuracy", 0) >= 3
        and dimensions.get("factual_reliability", 0) >= 3
        and dimensions.get("compliance_boundaries", 0) >= 3
    )
    changed = False
    for entry in cache.values():
        if entry.get("report_id") != report_id or entry.get("state") != "pending":
            continue
        entry["state"] = "approved" if approved else "rejected"
        entry["evaluation_id"] = evaluation.get("evaluation_id")
        entry["evaluation_scores"] = {
            key: dimensions.get(key, 0)
            for key in ("summary_accuracy", "factual_reliability", "compliance_boundaries")
        }
        changed = True
    if not changed:
        return path
    return write_json(
        path,
        {"schema_version": SEMANTIC_CACHE_SCHEMA, "items": cache},
    )
