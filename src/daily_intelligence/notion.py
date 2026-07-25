from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import yaml

from .config import project_root
from .reporting import reference_time_label, validate_evaluation_data, validate_report
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
from .utils import canonicalize_url, read_json, write_json

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
                "Accept": "application/json",
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
            for file_value in kwargs.get("files", {}).values():
                if (
                    isinstance(file_value, tuple)
                    and len(file_value) >= 2
                    and hasattr(file_value[1], "seek")
                ):
                    file_value[1].seek(0)
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

    def retrieve_file_upload(self, file_upload_id: str) -> dict[str, Any]:
        return self._request("GET", f"/file_uploads/{file_upload_id}")

    def upload_file(self, path: Path, content_type: str) -> str:
        if not path.is_file():
            raise FileNotFoundError(f"Notion image upload file does not exist: {path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Notion direct file uploads must not exceed 20 MB")
        created = self._request(
            "POST",
            "/file_uploads",
            json={
                "mode": "single_part",
                "filename": path.name,
                "content_type": content_type,
            },
        )
        file_upload_id = str(created["id"])
        with path.open("rb") as handle:
            uploaded = self._request(
                "POST",
                f"/file_uploads/{file_upload_id}/send",
                files={"file": (path.name, handle, content_type)},
            )
        if uploaded.get("status") != "uploaded":
            raise RuntimeError(
                f"Notion file upload {file_upload_id} ended with "
                f"status {uploaded.get('status')!r}"
            )
        return file_upload_id

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


def _image_block(
    image: dict[str, Any],
    image_uploads: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    upload_id = None
    if image_uploads and image.get("sha256"):
        upload_id = image_uploads.get(str(image["sha256"]))
    if upload_id:
        file_data: dict[str, Any] = {
            "type": "file_upload",
            "file_upload": {"id": upload_id},
        }
    else:
        source_url = image.get("source_url") or image.get("url")
        if not isinstance(source_url, str) or not source_url.startswith(
            ("http://", "https://")
        ):
            return None
        file_data = {
            "type": "external",
            "external": {"url": source_url},
        }
    caption = str(image.get("caption") or "")
    credit = str(image.get("credit") or "")
    caption_text = "｜来源：".join(part for part in (caption, credit) if part)
    return {
        "object": "block",
        "type": "image",
        "image": {
            **file_data,
            "caption": [_text(caption_text)] if caption_text else [],
        },
    }


def _event_block(
    item: dict[str, Any],
    image_uploads: dict[str, str] | None = None,
) -> dict[str, Any]:
    status = STATUS_LABELS.get(item["status"], item["status"])
    link = item["source_refs"][0]["url"]
    evidence_children: list[dict[str, Any]] = []
    for ref in item.get("source_refs", []):
        time_text = ""
        if time_info := reference_time_label(ref):
            time_label, time_value = time_info
            time_text = f"，{time_label}：{time_value}"
        label = f"{ACCESS_LABELS.get(ref['access'], ref['access'])}{time_text}"
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
    if isinstance(image, dict) and (image_block := _image_block(image, image_uploads)):
        children.insert(0, image_block)
    return {
        "object": "block",
        "type": "numbered_list_item",
        "numbered_list_item": {
            "rich_text": [_text(f"[{status}] {item['title']}", link, bold=True)],
            "color": "default",
            "children": children,
        },
    }


def _brief_block(
    item: dict[str, Any],
    image_uploads: dict[str, str] | None = None,
) -> dict[str, Any]:
    ref = item["source_ref"]
    status = STATUS_LABELS.get(item["status"], item["status"])
    source_rank = f" [{item['source_rank_label']}]" if item.get("source_rank_label") else ""
    children: list[dict[str, Any]] = []
    image = item.get("image")
    if isinstance(image, dict) and (image_block := _image_block(image, image_uploads)):
        children.append(image_block)
    if item.get("title_zh"):
        children.append(_block("paragraph", f"中文标题｜{item['title_zh']}"))
    if time_info := reference_time_label(ref):
        label, value = time_info
        children.append(_block("paragraph", f"{label}｜{value}"))
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


def report_to_blocks(
    report: dict[str, Any],
    image_uploads: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
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
                renderer = (
                    _brief_block
                    if report.get("schema_version") in {"1.5", "2.0"}
                    else _event_block
                )
                blocks.extend(renderer(item, image_uploads) for item in items)
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
                *[
                    _block("bulleted_list_item", f"因果链：{value}")
                    for value in analysis.get("causal_chain", [])
                ],
                *[
                    _block("bulleted_list_item", f"关键假设：{value}")
                    for value in analysis.get("assumptions", [])
                ],
                *[
                    _block("bulleted_list_item", f"证据缺口：{value}")
                    for value in analysis.get("evidence_gaps", [])
                ],
                *[
                    _block("paragraph", f"{label}：{analysis[key]}")
                    for label, key in (
                        ("时间跨度", "time_horizon"),
                        ("置信度依据", "confidence_rationale"),
                        ("相对上一版", "change_from_prior"),
                        ("决策相关性", "decision_relevance"),
                    )
                    if analysis.get(key)
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
    synthesis = report.get("cross_perspective_synthesis")
    if isinstance(synthesis, dict):
        blocks.append(_block("heading_2", "跨视角综合"))
        blocks.append(
            _callout(
                synthesis.get("overall_judgment", ""),
                "blue_background",
                "🧭",
            )
        )
        for value in synthesis.get("consensus", []):
            blocks.append(_block("bulleted_list_item", f"共同结论：{value}"))
        for tension in synthesis.get("tensions", []):
            if not isinstance(tension, dict):
                continue
            perspectives = "、".join(tension.get("perspectives", []))
            blocks.append(
                _block(
                    "bulleted_list_item",
                    f"分歧｜{tension.get('issue', '')}（{perspectives}）："
                    f"{tension.get('source_of_difference', '')}",
                )
            )
        for label, key in (
            ("传导链", "transmission_chain"),
            ("共同观察", "shared_watch_signals"),
            ("修正触发", "revision_triggers"),
        ):
            for value in synthesis.get(key, []):
                blocks.append(_block("bulleted_list_item", f"{label}：{value}"))
        blocks.append(
            _block(
                "paragraph",
                "综合引用事件：" + "、".join(synthesis.get("evidence_event_ids", [])),
            )
        )
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
    elif report.get("schema_version") in {"1.5", "2.0"}:
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


def _local_report_images(
    report: dict[str, Any],
    data_dir: Path,
) -> dict[str, tuple[Path, str]]:
    root = data_dir.resolve()
    images: dict[str, tuple[Path, str]] = {}
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        for collection in ("briefs", "items"):
            for item in section.get(collection, []):
                if not isinstance(item, dict) or not isinstance(item.get("image"), dict):
                    continue
                image = item["image"]
                digest = str(image.get("sha256") or "")
                relative_path = image.get("local_path")
                content_type = str(image.get("content_type") or "")
                if not digest or not isinstance(relative_path, str) or not content_type:
                    continue
                path = (root / relative_path).resolve()
                if not path.is_relative_to(root):
                    raise ValueError(
                        f"Report image path escapes the configured data root: {relative_path}"
                    )
                images.setdefault(digest, (path, content_type))
    return images


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _saved_upload_id(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("id"):
        return str(value["id"])
    return None


def _prepare_image_uploads(
    publisher: NotionPublisher,
    report: dict[str, Any],
    data_dir: Path,
    entry: dict[str, Any],
    on_progress: Callable[[], None],
) -> tuple[dict[str, str], dict[str, str]]:
    local_images = _local_report_images(report, data_dir)
    state = entry.setdefault("image_uploads", {})
    if not isinstance(state, dict):
        state = {}
        entry["image_uploads"] = state
    errors: dict[str, str] = {}
    resolved: dict[str, str] = {}
    for digest, (path, content_type) in local_images.items():
        saved_id = _saved_upload_id(state.get(digest))
        if saved_id:
            try:
                saved = publisher.retrieve_file_upload(saved_id)
            except Exception:
                saved = {}
            if saved.get("status") == "uploaded":
                resolved[digest] = saved_id
                continue
        try:
            if not path.is_file():
                raise FileNotFoundError(f"local image file is missing: {path}")
            if _file_sha256(path) != digest:
                raise ValueError(f"local image hash does not match report metadata: {path}")
            file_upload_id = publisher.upload_file(path, content_type)
        except Exception as exc:
            state.pop(digest, None)
            errors[digest] = f"{type(exc).__name__}: {str(exc)[:500]}"
        else:
            state[digest] = {
                "id": file_upload_id,
                "local_path": str(path.relative_to(data_dir.resolve()).as_posix()),
                "content_type": content_type,
            }
            resolved[digest] = file_upload_id
        entry["image_upload_errors"] = errors
        on_progress()
    entry["image_upload_errors"] = errors
    return resolved, errors


def _block_plain_text(block: dict[str, Any]) -> str:
    kind = block.get("type")
    if not isinstance(kind, str):
        return ""
    rich_text = block.get(kind, {}).get("rich_text", [])
    return "".join(
        str(item.get("plain_text") or item.get("text", {}).get("content") or "")
        for item in rich_text
        if isinstance(item, dict)
    )


def _block_link_url(block: dict[str, Any]) -> str | None:
    kind = block.get("type")
    if not isinstance(kind, str):
        return None
    for item in block.get(kind, {}).get("rich_text", []):
        if not isinstance(item, dict):
            continue
        url = item.get("href") or item.get("text", {}).get("link", {}).get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return url
    return None


def _edition_story_blocks(
    blocks: list[dict[str, Any]],
    edition: str,
) -> list[dict[str, Any]]:
    label = "06:00 早报" if edition == "morning" else "18:00 晚报"
    in_edition = False
    stories: list[dict[str, Any]] = []
    for block in blocks:
        if in_edition and block.get("type") == "divider":
            break
        if (
            block.get("type") == "heading_2"
            and _block_plain_text(block).strip() == label
        ):
            in_edition = True
            continue
        if in_edition and block.get("type") == "numbered_list_item":
            stories.append(block)
    if not in_edition:
        raise RuntimeError(
            f"Could not find the {label!r} section on the registered Notion page"
        )
    return stories


def _report_items_with_images(
    report: dict[str, Any],
) -> list[tuple[str, str, dict[str, Any]]]:
    items: list[tuple[str, str, dict[str, Any]]] = []
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        for collection in ("briefs", "items"):
            for item in section.get(collection, []):
                if not isinstance(item, dict) or not isinstance(item.get("image"), dict):
                    continue
                ref = item.get("source_ref")
                if not isinstance(ref, dict):
                    refs = item.get("source_refs", [])
                    ref = refs[0] if isinstance(refs, list) and refs else {}
                url = ref.get("url") if isinstance(ref, dict) else None
                if isinstance(url, str) and url.startswith(("http://", "https://")):
                    items.append((str(item.get("item_id") or ""), url, item["image"]))
    return items


def backfill_report_images(
    report_path: Path,
    data_dir: Path,
    config_path: Path | None = None,
) -> tuple[str, str]:
    """Append missing images to existing story blocks without duplicating the edition."""
    errors, _warnings = validate_report(report_path)
    if errors:
        raise ValueError("Report validation failed: " + "; ".join(errors))
    report = read_json(report_path)
    if not isinstance(report, dict):
        raise ValueError("Report must be a JSON object")
    image_items = _report_items_with_images(report)
    if not image_items:
        raise ValueError("Report contains no materialized images to backfill")

    token = os.getenv("NOTION_TOKEN")
    data_source_id = os.getenv("NOTION_DATA_SOURCE_ID")
    if not token or not data_source_id:
        raise RuntimeError("NOTION_TOKEN and NOTION_DATA_SOURCE_ID are required")

    registry_path = data_dir / "publishing" / "notion-registry.json"
    registry = read_json(registry_path) if registry_path.exists() else {}
    if not isinstance(registry, dict):
        raise ValueError("Notion publishing registry must be a JSON object")
    key = f"{report['date']}:{report['edition']}"
    entry = registry.get(key)
    if not isinstance(entry, dict) or not entry.get("page_id"):
        raise RuntimeError("Publish the report before backfilling its images")

    page_id = str(entry["page_id"])
    progress = {
        "status": "publishing",
        "report_id": report["report_id"],
        "revision": report["revision"],
        "target_images": len(image_items),
        "appended": 0,
        "already_present": 0,
        "missing_item_ids": [],
    }
    entry["image_backfill"] = progress
    write_json(registry_path, registry)

    publisher = NotionPublisher(token, data_source_id, config_path)
    try:
        def save_progress() -> None:
            write_json(registry_path, registry)

        image_uploads, image_upload_errors = _prepare_image_uploads(
            publisher,
            report,
            data_dir,
            entry,
            save_progress,
        )
        stories = _edition_story_blocks(
            publisher.retrieve_blocks(page_id),
            str(report["edition"]),
        )
        stories_by_url: dict[str, list[dict[str, Any]]] = {}
        for story in stories:
            url = _block_link_url(story)
            if url:
                stories_by_url.setdefault(canonicalize_url(url), []).append(story)

        used_block_ids: set[str] = set()
        for item_id, url, image in image_items:
            matches = [
                block
                for block in stories_by_url.get(canonicalize_url(url), [])
                if str(block.get("id") or "") not in used_block_ids
            ]
            if not matches:
                progress["missing_item_ids"].append(item_id)
                save_progress()
                continue
            story = matches[0]
            story_id = str(story.get("id") or "")
            if not story_id:
                progress["missing_item_ids"].append(item_id)
                save_progress()
                continue
            used_block_ids.add(story_id)
            children = publisher.retrieve_blocks(story_id)
            if any(child.get("type") == "image" for child in children):
                progress["already_present"] += 1
                save_progress()
                continue
            image_block = _image_block(image, image_uploads)
            if image_block is None:
                progress["missing_item_ids"].append(item_id)
                save_progress()
                continue
            publisher.append_blocks(story_id, [image_block])
            progress["appended"] += 1
            save_progress()
    finally:
        publisher.close()

    progress["status"] = "partial" if progress["missing_item_ids"] else "complete"
    entry["media_report_id"] = report["report_id"]
    entry["media_revision"] = report["revision"]
    entry["media_status"] = (
        "external_fallback" if image_upload_errors else "uploaded"
    )
    write_json(registry_path, registry)
    if progress["missing_item_ids"]:
        status = "images_backfilled_partial"
    elif progress["appended"] == 0:
        status = "skipped_already_present"
    elif image_upload_errors:
        status = "images_backfilled_with_fallbacks"
    else:
        status = "images_backfilled"
    return page_id, status


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

        prior_uploads = (
            existing.get("image_uploads", {})
            if isinstance(existing, dict)
            and existing.get("report_id") == report["report_id"]
            else {}
        )
        entry = {
            "page_id": page_id,
            "report_id": report["report_id"],
            "revision": report["revision"],
            "published_at": report["generated_at"],
            "status": "publishing",
            "blocks_appended": start_block,
            "blocks_total": 0,
            "image_uploads": prior_uploads,
            "image_upload_errors": {},
        }
        if isinstance(existing, dict) and existing.get("evaluation_ids"):
            entry["evaluation_ids"] = existing["evaluation_ids"]
        registry[key] = entry
        write_json(registry_path, registry)

        def save_image_progress() -> None:
            write_json(registry_path, registry)

        image_uploads, image_upload_errors = _prepare_image_uploads(
            publisher,
            report,
            data_dir,
            entry,
            save_image_progress,
        )
        blocks = (
            report_to_blocks(report, image_uploads)
            if image_uploads
            else report_to_blocks(report)
        )
        entry["blocks_total"] = len(blocks)
        entry["media_status"] = (
            "external_fallback"
            if image_upload_errors
            else "uploaded"
            if image_uploads
            else "no_local_images"
        )
        write_json(registry_path, registry)

        def save_progress(completed: int) -> None:
            entry["blocks_appended"] = completed
            write_json(registry_path, registry)

        publisher.append_blocks(
            page_id,
            blocks,
            start_block=start_block,
            on_progress=save_progress,
        )
        entry["status"] = "complete"
        entry["blocks_appended"] = len(blocks)
        write_json(registry_path, registry)
        status = "published_with_image_fallbacks" if image_upload_errors else "published"
        return page_id, status
    finally:
        publisher.close()
