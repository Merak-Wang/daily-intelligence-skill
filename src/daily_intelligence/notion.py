from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import project_root
from .reporting import validate_evaluation_data, validate_report
from .reports import (
    ACCESS_LABELS,
    ANALYSIS_SECTION_LABELS,
    DOMAIN_LABELS,
    EVALUATION_LABELS,
    GROUP_LABELS,
    PERSPECTIVE_LABELS,
    STATE_LABELS,
    STATUS_LABELS,
    group_items_by_source,
    ordered_sections,
)
from .utils import read_json, write_json

_PROPERTY_TYPES: dict[str, set[str]] = {
    "title": {"title"},
    "date": {"date"},
    "version": {"select"},
    "status": {"status", "select"},
    "source": {"select"},
    "tags": {"multi_select"},
    "source_count": {"number"},
    "event_count": {"number"},
    "pending_verification_count": {"number"},
}
_FEEDBACK_PREFIX = "用户反馈|"


def validate_notion_schema(config: dict[str, Any], schema: dict[str, Any]) -> None:
    configured = config.get("properties", {})
    actual_properties = schema.get("properties", {})
    errors: list[str] = []

    for required in ("title", "date"):
        if not configured.get(required):
            errors.append(f"missing required mapping properties.{required}")

    for key, name in configured.items():
        allowed_types = _PROPERTY_TYPES.get(key)
        if allowed_types is None:
            errors.append(f"unsupported property mapping: {key}")
            continue
        actual = actual_properties.get(name)
        if actual is None:
            errors.append(f"{key} maps to missing property {name!r}")
            continue
        actual_type = actual.get("type")
        if actual_type not in allowed_types:
            expected = " or ".join(sorted(allowed_types))
            errors.append(f"{name!r} has type {actual_type!r}; expected {expected}")

    status_name = configured.get("status")
    if status_name and status_name in actual_properties:
        status_property = actual_properties[status_name]
        status_type = status_property.get("type")
        option_container = status_property.get(status_type, {}) if status_type else {}
        option_names = {option.get("name") for option in option_container.get("options", [])}
        if option_names:
            values = config.get("values", {})
            for value_key in ("morning_status", "evening_status"):
                status_value = values.get(value_key)
                if status_value and status_value not in option_names:
                    errors.append(
                        f"{value_key}={status_value!r} is not an option of {status_name!r}"
                    )

    if errors:
        raise ValueError(
            "Notion data source schema mismatch: "
            + "; ".join(errors)
            + ". Update configs/notion.yaml or the Notion data source schema."
        )


def resolve_notion_mapping(config: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if config.get("properties"):
        validate_notion_schema(config, schema)
        return config

    profiles = config.get("schema_profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("Notion config must define properties or schema_profiles")

    failures: list[str] = []
    for name, profile in profiles.items():
        if not isinstance(profile, dict):
            failures.append(f"{name}: profile must be an object")
            continue
        try:
            validate_notion_schema(profile, schema)
        except ValueError as exc:
            failures.append(f"{name}: {exc}")
            continue
        resolved = dict(profile)
        resolved["profile"] = name
        return resolved

    raise ValueError(
        "Notion data source did not match any configured schema profile: " + " | ".join(failures)
    )


class NotionPublisher:
    def __init__(self, token: str, data_source_id: str, config_path: Path | None = None):
        config_file = config_path or project_root() / "configs" / "notion.yaml"
        self.config = yaml.safe_load(config_file.read_text(encoding="utf-8"))
        self.data_source_id = data_source_id
        self.client = httpx.Client(
            base_url="https://api.notion.com/v1",
            headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": self.config.get("api_version", "2026-03-11"),
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        self.schema = self._request("GET", f"/data_sources/{data_source_id}")
        try:
            self.mapping = resolve_notion_mapping(self.config, self.schema)
        except Exception:
            self.client.close()
            raise

    def close(self) -> None:
        self.client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        delay = 1.0
        for attempt in range(5):
            response = self.client.request(method, path, **kwargs)
            if response.status_code != 429:
                response.raise_for_status()
                return response.json()
            if attempt == 4:
                response.raise_for_status()
            retry_after = float(response.headers.get("retry-after", delay))
            time.sleep(retry_after)
            delay *= 2
        raise RuntimeError("Unreachable")

    def find_page(self, report_date: str) -> str | None:
        date_property = self.mapping["properties"]["date"]
        if date_property not in self.schema.get("properties", {}):
            return None
        payload = {
            "filter": {
                "property": date_property,
                "date": {"equals": report_date},
            },
            "page_size": 10,
        }
        result = self._request("POST", f"/data_sources/{self.data_source_id}/query", json=payload)
        pages = result.get("results", [])
        return pages[0]["id"] if pages else None

    def build_properties(self, report: dict[str, Any]) -> dict[str, Any]:
        configured = self.mapping["properties"]
        schema = self.schema.get("properties", {})
        values = self.mapping.get("values", {})
        edition = report["edition"]
        desired: list[tuple[str, dict[str, Any]]] = []

        def add(key: str, value: dict[str, Any] | None) -> None:
            name = configured.get(key)
            if name and value is not None:
                desired.append((name, value))

        add("title", {"title": [_text(report["title"])]})
        add("date", {"date": {"start": report["date"]}})
        version = values.get(f"{edition}_version")
        add("version", {"select": {"name": version}} if version else None)
        status = values.get(f"{edition}_status")
        add("status", {"status": {"name": status}} if status else None)
        source = values.get("source")
        add("source", {"select": {"name": source}} if source else None)
        tags = values.get(f"{edition}_tags", values.get("tags", []))
        add(
            "tags",
            {"multi_select": [{"name": str(tag)} for tag in tags]} if tags else None,
        )
        add("source_count", {"number": report.get("source_count", 0)})
        add("event_count", {"number": report.get("event_count", 0)})
        add(
            "pending_verification_count",
            {"number": len(report.get("pending_verifications", []))},
        )

        properties: dict[str, Any] = {}
        for name, value in desired:
            expected = schema[name]["type"]
            actual = next(iter(value))
            if expected != actual and {expected, actual} <= {"select", "status"}:
                value = {expected: value[actual]}
            properties[name] = value
        return properties

    def create_page(self, report: dict[str, Any]) -> str:
        payload = {
            "parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
            "properties": self.build_properties(report),
        }
        result = self._request("POST", "/pages", json=payload)
        return result["id"]

    def update_properties(self, page_id: str, report: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            f"/pages/{page_id}",
            json={"properties": self.build_properties(report)},
        )

    def append_blocks(
        self,
        page_id: str,
        blocks: list[dict[str, Any]],
        start_block: int = 0,
        on_progress: Callable[[int], None] | None = None,
    ) -> int:
        for start in range(start_block, len(blocks), 100):
            completed = min(start + 100, len(blocks))
            self._request(
                "PATCH",
                f"/blocks/{page_id}/children",
                json={"children": blocks[start:completed]},
            )
            if on_progress:
                on_progress(completed)
        return len(blocks)

    def retrieve_blocks(self, page_id: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            payload = self._request("GET", f"/blocks/{page_id}/children", params=params)
            blocks.extend(payload.get("results", []))
            if not payload.get("has_more"):
                return blocks
            cursor = payload.get("next_cursor")


def _text(content: str, url: str | None = None, bold: bool = False) -> dict[str, Any]:
    text: dict[str, Any] = {"content": content[:2000]}
    if url:
        text["link"] = {"url": url}
    result: dict[str, Any] = {"type": "text", "text": text}
    if bold:
        result["annotations"] = {
            "bold": True,
            "italic": False,
            "strikethrough": False,
            "underline": False,
            "code": False,
            "color": "default",
        }
    return result


def _block(kind: str, text: str, color: str = "default") -> dict[str, Any]:
    return {
        "object": "block",
        "type": kind,
        kind: {"rich_text": [_text(text)], "color": color},
    }


def _callout(text: str, color: str = "blue_background", icon: str = "💡") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": [_text(text)],
            "icon": {"type": "emoji", "emoji": icon},
            "color": color,
        },
    }


def _event_block(item: dict[str, Any]) -> dict[str, Any]:
    status = STATUS_LABELS.get(item["status"], item["status"])
    link = item["source_refs"][0]["url"]
    evidence_children: list[dict[str, Any]] = []
    for ref in item.get("source_refs", []):
        published = f"，发布于 {ref['published_at']}" if ref.get("published_at") else ""
        label = f"{ACCESS_LABELS.get(ref['access'], ref['access'])}{published}"
        evidence_children.append(
            {
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [_text(f"{ref['title']}（{label}）", ref["url"])]
                },
            }
        )
    evidence_children.extend(
        _block("paragraph", f"证据说明：{note}", "gray_background")
        for note in item.get("evidence_notes", [])
    )
    children: list[dict[str, Any]] = [
        _block("paragraph", f"TL;DR｜{item['tldr']}"),
        _callout(
            f"重要性 {item['importance']}/100 · 置信度 {item['confidence']:.2f}｜"
            f"{item['why_it_matters']}",
            "yellow_background",
            "⭐",
        ),
        {
            "object": "block",
            "type": "toggle",
            "toggle": {
                "rich_text": [_text("证据、原文与访问状态")],
                "color": "gray_background",
                "children": evidence_children,
            },
        },
    ]
    image = item.get("image")
    if image:
        children.append(
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "external",
                    "external": {"url": image["url"]},
                    "caption": [_text(f"{image['caption']}｜来源：{image['credit']}")],
                },
            }
        )
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {
            "rich_text": [_text(f"[{status}] {item['title']}", link, bold=True)],
            "color": "default",
            "children": children,
        },
    }


def _brief_block(item: dict[str, Any]) -> dict[str, Any]:
    ref = item["source_ref"]
    status = STATUS_LABELS.get(item["status"], item["status"])
    source_rank = f" [{item['source_rank_label']}]" if item.get("source_rank_label") else ""
    children: list[dict[str, Any]] = []
    if item.get("title_zh"):
        children.append(_block("paragraph", f"中文标题｜{item['title_zh']}"))
    children.append(_block("paragraph", f"TL;DR｜{item['tldr']}"))
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {
            "rich_text": [
                _text(f"[{status}] {item['title']}{source_rank}", ref["url"], bold=True)
            ],
            "color": "default",
            "children": children,
        },
    }


def _evaluation_table(dimensions: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [["维度", "得分", "重点结论"]] + [
        [EVALUATION_LABELS.get(item["id"], item["id"]), f"{item['score']}/5", item["finding"]]
        for item in dimensions
    ]
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": 3,
            "has_column_header": True,
            "has_row_header": False,
            "children": [
                {
                    "object": "block",
                    "type": "table_row",
                    "table_row": {"cells": [[_text(cell)] for cell in row]},
                }
                for row in rows
            ],
        },
    }


def parse_user_feedback(text: str) -> dict[str, Any] | None:
    if not text.startswith(_FEEDBACK_PREFIX):
        return None
    labels = {
        "relevance": "相关性",
        "accuracy": "准确性",
        "analysis_value": "分析价值",
        "overall_satisfaction": "整体满意度",
    }
    scores: dict[str, int] = {}
    for key, label in labels.items():
        match = re.search(rf"{label}\s*=\s*([1-5])", text)
        if match:
            scores[key] = int(match.group(1))
    comment = text.split("补充意见=", 1)[1].strip() if "补充意见=" in text else ""
    if not scores and not comment:
        return None
    return {"scores": scores, "comment": comment}


def sync_user_feedback(data_dir: Path, config_path: Path | None = None) -> Path | None:
    token = os.getenv("NOTION_TOKEN")
    data_source_id = os.getenv("NOTION_DATA_SOURCE_ID")
    registry_path = data_dir / "publishing" / "notion-registry.json"
    if not token or not data_source_id or not registry_path.exists():
        return None
    registry = read_json(registry_path)
    if not isinstance(registry, dict):
        raise ValueError("Notion publishing registry must be a JSON object")
    entries = sorted(
        (item for item in registry.values() if isinstance(item, dict) and item.get("page_id")),
        key=lambda item: str(item.get("published_at", "")),
        reverse=True,
    )
    if not entries:
        return None
    publisher = NotionPublisher(token, data_source_id, config_path)
    try:
        blocks = publisher.retrieve_blocks(entries[0]["page_id"])
    finally:
        publisher.close()
    feedback_items = []
    for block in blocks:
        kind = block.get("type")
        rich_text = block.get(kind, {}).get("rich_text", []) if kind else []
        text = "".join(item.get("plain_text", "") for item in rich_text)
        parsed = parse_user_feedback(text)
        if parsed:
            feedback_items.append(
                {
                    "feedback_id": block.get("id"),
                    "page_id": entries[0]["page_id"],
                    "captured_at": entries[0].get("published_at"),
                    **parsed,
                }
            )
    if not feedback_items:
        return None
    path = data_dir / "state" / "user-feedback.json"
    write_json(path, {"schema_version": "1.0", "items": feedback_items[-5:]})
    return path


def report_to_blocks(report: dict[str, Any]) -> list[dict[str, Any]]:
    edition_label = "06:00 早报" if report["edition"] == "morning" else "18:00 晚报"
    blocks: list[dict[str, Any]] = [
        {"object": "block", "type": "divider", "divider": {}},
        _block("heading_2", edition_label, "blue_background"),
        _block("paragraph", f"生成时间：{report['generated_at']} · 修订号：{report['revision']}"),
        _callout(
            "\n".join(report.get("executive_summary", [])) or "本版暂无摘要。",
            "blue_background",
            "🧭",
        ),
        {"object": "block", "type": "table_of_contents", "table_of_contents": {}},
    ]
    for module in ("information", "technology"):
        color = "blue_background" if module == "information" else "purple_background"
        blocks.append(_block("heading_1", GROUP_LABELS[module], color))
        for section in ordered_sections(report, module):
            blocks.append(_block("heading_2", section.get("title", "未命名栏目")))
            if not section.get("items") and not section.get("briefs"):
                blocks.append(
                    _block(
                        "paragraph",
                        section.get("coverage_note", "本时段暂无内容。"),
                        "gray_background",
                    )
                )
            for source, items in group_items_by_source(section):
                blocks.append(
                    {
                        "object": "block",
                        "type": "heading_3",
                        "heading_3": {
                            "rich_text": [_text(source["name"], source["url"], bold=True)],
                            "color": "default",
                        },
                    }
                )
                renderer = _brief_block if report.get("schema_version") == "1.5" else _event_block
                blocks.extend(renderer(item) for item in items)
        if module == "information" and report.get("pending_verifications"):
            blocks.append(
                _callout(
                    "以下来源访问失败，保留链接供人工查看。",
                    "gray_background",
                    "🔒",
                )
            )
            for pending in report["pending_verifications"]:
                blocks.append(
                    {
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [
                                _text(
                                    f"{pending['source_name']}："
                                    f"{pending.get('note', pending['status'])}",
                                    pending.get("url"),
                                )
                            ]
                        },
                    }
                )

    blocks.append(_block("heading_1", "研判", "orange_background"))
    if report.get("analyses"):
        last_domain = None
        for analysis in report["analyses"]:
            domain = analysis.get("domain")
            if domain != last_domain:
                blocks.append(
                    _block(
                        "heading_2",
                        ANALYSIS_SECTION_LABELS.get(
                            domain, DOMAIN_LABELS.get(domain, str(domain))
                        ),
                    )
                )
                last_domain = domain
            blocks.append(_block("heading_3", analysis["claim"]))
            perspectives = "、".join(
                PERSPECTIVE_LABELS.get(item, item)
                for item in analysis.get("perspectives", [])
            )
            blocks.append(
                _callout(
                    "视角："
                    f"{perspectives or DOMAIN_LABELS.get(analysis['domain'], analysis['domain'])}｜"
                    f"置信度 {analysis['confidence']:.2f}｜"
                    f"变化 {STATE_LABELS.get(analysis['state_change'], analysis['state_change'])}",
                    "orange_background",
                    "🔎",
                )
            )
            if analysis.get("narrative"):
                blocks.append(_block("quote", analysis["narrative"]))
            if analysis.get("historical_context"):
                blocks.append(_callout(analysis["historical_context"], "brown_background", "📚"))
            if analysis.get("dialectical_analysis"):
                blocks.append(_callout(analysis["dialectical_analysis"], "yellow_background", "⚖️"))
            for position in analysis.get("stakeholder_positions", []):
                blocks.append(
                    _block(
                        "bulleted_list_item",
                        f"{position['stakeholder']}｜{position['position']} "
                        f"利益基础：{position['interests']}",
                    )
                )
            details = [
                _block("paragraph", "证据事件：" + ", ".join(analysis["evidence_event_ids"])),
                *[
                    _block("bulleted_list_item", f"事实：{value}")
                    for value in analysis.get("facts", [])
                ],
                _block("paragraph", f"推理链：{analysis.get('reasoning', '')}"),
                *[
                    _block("bulleted_list_item", f"反证：{value}")
                    for value in analysis.get("counter_evidence", [])
                ],
                *[
                    _block("bulleted_list_item", f"情景：{value}")
                    for value in analysis.get("scenarios", [])
                ],
                *[
                    _block("bulleted_list_item", f"建议：{value}")
                    for value in analysis.get("actions", [])
                ],
                *[
                    _block("bulleted_list_item", f"观察：{value}")
                    for value in analysis.get("watch_signals", [])
                ],
            ]
            blocks.append(
                {
                    "object": "block",
                    "type": "toggle",
                    "toggle": {
                        "rich_text": [_text("证据链、反证、情景与建议")],
                        "color": "gray_background",
                        "children": details,
                    },
                }
            )
    else:
        blocks.append(_block("paragraph", "本版没有形成达到证据门槛的研判。"))
    if report.get("changes"):
        blocks.append(_block("heading_2", "日间新增、确认与修正"))
        for change in report["changes"]:
            blocks.append(_block("bulleted_list_item", change))
    if report.get("tomorrow_watch_items"):
        blocks.append(_block("heading_2", "次日观察项"))
        for item in report["tomorrow_watch_items"]:
            blocks.append(_block("bulleted_list_item", item))

    evaluation = report.get("quality_evaluation")
    if evaluation:
        blocks.append(_block("heading_1", "质量评估与用户反馈", "green_background"))
        blocks.append(
            _callout(
                f"独立评估总分：{evaluation['total_score']}/45｜"
                f"连续性建议：{evaluation['continuity_decision']}",
                "green_background",
                "✅",
            )
        )
        blocks.append(_evaluation_table(evaluation["dimensions"]))
        for title, key in (
            ("主要缺陷", "main_defects"),
            ("证据不足项", "insufficient_evidence"),
            ("改进建议", "improvements"),
        ):
            blocks.append(_block("heading_2", title))
            values = evaluation.get(key, []) or ["无"]
            blocks.extend(_block("bulleted_list_item", value) for value in values)
        blocks.append(
            _callout(
                "请直接编辑下一行评分；这些反馈会在后续日报中同步使用。",
                "pink_background",
                "📝",
            )
        )
        blocks.append(
            _block(
                "quote",
                "用户反馈|相关性=|准确性=|分析价值=|整体满意度=|补充意见=",
            )
        )
    elif report.get("schema_version") == "1.5":
        blocks.append(_block("heading_1", "质量评估与用户反馈", "green_background"))
        blocks.append(
            _callout(
                "独立评估将在发布后异步补充；评估仅提供修改建议，不阻塞日报发布。",
                "gray_background",
                "⏳",
            )
        )
        blocks.append(
            _block(
                "quote",
                "用户反馈|相关性=|准确性=|分析价值=|整体满意度=|补充意见=",
            )
        )
    return blocks


def evaluation_to_blocks(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = [
        _block("heading_2", "独立评估结果（发布后补充）", "green_background"),
        _callout(
            f"总分：{evaluation['total_score']}/45｜"
            f"连续性建议：{evaluation['continuity_decision']}",
            "green_background",
            "✅",
        ),
        _evaluation_table(evaluation["dimensions"]),
    ]
    for title, key in (
        ("主要缺陷", "main_defects"),
        ("证据不足项", "insufficient_evidence"),
        ("改进建议", "improvements"),
    ):
        blocks.append(_block("heading_3", title))
        blocks.extend(
            _block("bulleted_list_item", value)
            for value in (evaluation.get(key) or ["无"])
        )
    return blocks


def append_evaluation(
    report_path: Path,
    evaluation_path: Path,
    data_dir: Path,
    config_path: Path | None = None,
) -> tuple[str, str]:
    report = read_json(report_path)
    evaluation = read_json(evaluation_path)
    errors = validate_evaluation_data(evaluation, report)
    if errors:
        raise ValueError("Evaluation validation failed: " + "; ".join(errors))
    token = os.getenv("NOTION_TOKEN")
    data_source_id = os.getenv("NOTION_DATA_SOURCE_ID")
    if not token or not data_source_id:
        raise RuntimeError("NOTION_TOKEN and NOTION_DATA_SOURCE_ID are required")
    registry_path = data_dir / "publishing" / "notion-registry.json"
    registry = read_json(registry_path) if registry_path.exists() else {}
    key = f"{report['date']}:{report['edition']}"
    entry = registry.get(key) if isinstance(registry, dict) else None
    if not isinstance(entry, dict) or not entry.get("page_id"):
        raise RuntimeError("Publish the report before appending its independent evaluation")
    evaluation_id = evaluation["evaluation_id"]
    if evaluation_id in entry.get("evaluation_ids", []):
        return str(entry["page_id"]), "skipped_duplicate"
    publisher = NotionPublisher(token, data_source_id, config_path)
    try:
        publisher.append_blocks(str(entry["page_id"]), evaluation_to_blocks(evaluation))
    finally:
        publisher.close()
    entry.setdefault("evaluation_ids", []).append(evaluation_id)
    entry["evaluation_status"] = "completed"
    write_json(registry_path, registry)
    return str(entry["page_id"]), "appended"


def publish_report(
    report_path: Path,
    data_dir: Path,
    force: bool = False,
    config_path: Path | None = None,
) -> tuple[str, str]:
    errors, _warnings = validate_report(report_path)
    if errors:
        raise ValueError("Report validation failed: " + "; ".join(errors))

    report = read_json(report_path)
    if not isinstance(report, dict):
        raise ValueError("Report must be a JSON object")

    token = os.getenv("NOTION_TOKEN")
    data_source_id = os.getenv("NOTION_DATA_SOURCE_ID")
    if not token or not data_source_id:
        raise RuntimeError("NOTION_TOKEN and NOTION_DATA_SOURCE_ID are required")

    registry_path = data_dir / "publishing" / "notion-registry.json"
    registry = read_json(registry_path) if registry_path.exists() else {}
    if not isinstance(registry, dict):
        raise ValueError("Notion publishing registry must be a JSON object")

    key = f"{report['date']}:{report['edition']}"
    existing = registry.get(key)
    if isinstance(existing, dict) and not force:
        if existing.get("status", "complete") == "complete":
            return existing["page_id"], "skipped_duplicate"
        if existing.get("report_id") != report["report_id"]:
            raise RuntimeError(
                "An interrupted publish exists for a different report_id; "
                "resolve it before publishing this edition"
            )

    publisher = NotionPublisher(token, data_source_id, config_path)
    try:
        if isinstance(existing, dict) and not force:
            page_id = existing["page_id"]
            start_block = int(existing.get("blocks_appended", 0))
            publisher.update_properties(page_id, report)
        else:
            page_id = publisher.find_page(report["date"])
            if not page_id:
                page_id = publisher.create_page(report)
            else:
                publisher.update_properties(page_id, report)
            start_block = 0

        blocks = report_to_blocks(report)
        registry[key] = {
            "page_id": page_id,
            "report_id": report["report_id"],
            "revision": report["revision"],
            "published_at": report["generated_at"],
            "status": "publishing",
            "blocks_appended": start_block,
            "blocks_total": len(blocks),
        }
        write_json(registry_path, registry)

        def save_progress(completed: int) -> None:
            registry[key]["blocks_appended"] = completed
            write_json(registry_path, registry)

        publisher.append_blocks(
            page_id,
            blocks,
            start_block=start_block,
            on_progress=save_progress,
        )
        registry[key]["status"] = "complete"
        registry[key]["blocks_appended"] = len(blocks)
        write_json(registry_path, registry)
        return page_id, "published"
    finally:
        publisher.close()
