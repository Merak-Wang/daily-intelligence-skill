from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import MediaConfig, OutputConfig
from .local_output import write_local_outputs
from .localization import is_chinese_output, localized, translated_title
from .media import materialize_report_images
from .reporting import (
    compile_report_data,
    normalize_report_data,
    reference_time_label,
    report_content_hash,
    split_narrative_paragraphs,
    validate_evaluation_data,
    validate_report_data,
)
from .semantics import (
    finalize_semantic_cache_evaluation,
    load_semantic_cache,
    update_semantic_cache_from_report,
)
from .state import update_continuity_state
from .storage import next_revision, write_immutable_json, write_text_atomic
from .taxonomy import SECTION_GROUPS_V13
from .utils import read_json, write_json

EDITION_LABELS = {"morning": "早报", "evening": "晚报"}
EDITION_LABELS_EN = {"morning": "Morning Brief", "evening": "Evening Brief"}
STATUS_LABELS = {
    "NEW": "新增",
    "UPD": "更新",
    "CONF": "确认",
    "REV": "修正",
    "WATCH": "观察",
    "CLOSED": "关闭",
}
STATUS_LABELS_EN = {
    "NEW": "New",
    "UPD": "Updated",
    "CONF": "Confirmed",
    "REV": "Revised",
    "WATCH": "Watch",
    "CLOSED": "Closed",
}
DOMAIN_LABELS = {
    "geopolitics": "地缘政治",
    "markets": "市场与经济",
    "ai_technology": "人工智能与技术",
}
DOMAIN_LABELS_EN = {
    "geopolitics": "Geopolitics",
    "markets": "Markets and Economy",
    "ai_technology": "Artificial Intelligence and Technology",
}
STATE_LABELS = {
    "new": "新观点",
    "strengthening": "增强",
    "unchanged": "不变",
    "weakening": "减弱",
    "revised": "修正",
    "invalidated": "失效",
    "closed": "关闭",
}
STATE_LABELS_EN = {
    "new": "New",
    "strengthening": "Strengthening",
    "unchanged": "Unchanged",
    "weakening": "Weakening",
    "revised": "Revised",
    "invalidated": "Invalidated",
    "closed": "Closed",
}
ACCESS_LABELS = {
    "full_text": "已读正文",
    "partial": "已读部分正文",
    "metadata_only": "仅元数据",
    "verification_required": "需要人工验证",
}
ACCESS_LABELS_EN = {
    "full_text": "Full text read",
    "partial": "Partial text read",
    "metadata_only": "Metadata only",
    "verification_required": "Manual verification required",
}

GROUP_LABELS = {"information": "资讯", "technology": "技术"}
GROUP_LABELS_EN = {"information": "News", "technology": "Technology"}
ASSESSMENT_LABELS = {
    "trend": "趋势判断",
    "risk": "风险分析",
    "learning_research": "学习与研究建议",
}
ASSESSMENT_LABELS_EN = {
    "trend": "Trend",
    "risk": "Risk",
    "learning_research": "Learning and Research",
}
PERSPECTIVE_LABELS = {
    "geopolitics": "地缘政治专家",
    "ai_research_engineering": "AI研究与开发工程师",
    "equity_analysis": "股票分析师",
    "china_standpoint": "中国立场",
    "western_standpoint": "西方立场",
}
PERSPECTIVE_LABELS_EN = {
    "geopolitics": "Geopolitical Expert",
    "ai_research_engineering": "AI Research and Engineering",
    "equity_analysis": "Equity Analyst",
    "china_standpoint": "Chinese Perspective",
    "western_standpoint": "Western Perspective",
}
ANALYSIS_SECTION_LABELS = {
    "geopolitics": "从地缘政治专家的角度",
    "ai_technology": "从 AI 研究/开发工程师的角度",
    "markets": "从股票分析师的角度",
}
ANALYSIS_SECTION_LABELS_EN = {
    "geopolitics": "Geopolitical Perspective",
    "ai_technology": "AI Research and Engineering Perspective",
    "markets": "Equity-Market Perspective",
}
BRIEF_REPORT_SCHEMAS = {"1.5", "2.0"}
EVALUATION_LABELS = {
    "coverage": "信息覆盖度",
    "importance_ordering": "重要性排序",
    "factual_reliability": "事实可靠性",
    "summary_accuracy": "摘要准确性",
    "analysis_traceability": "分析可追溯性",
    "historical_continuity": "历史连续性",
    "readability": "可读性",
    "timeliness": "时效性",
    "compliance_boundaries": "合规与边界",
}
EVALUATION_LABELS_EN = {
    "coverage": "Coverage",
    "importance_ordering": "Importance Ordering",
    "factual_reliability": "Factual Reliability",
    "summary_accuracy": "Summary Accuracy",
    "analysis_traceability": "Analysis Traceability",
    "historical_continuity": "Historical Continuity",
    "readability": "Readability",
    "timeliness": "Timeliness",
    "compliance_boundaries": "Compliance and Boundaries",
}


def ordered_sections(report: dict[str, Any], module: str) -> list[dict[str, Any]]:
    """处理：按报告契约顺序返回现有栏目。
    输入：
    - ``report``：当前报告结构；包含栏目、简报或事件、来源引用及质量元数据。
    - ``module``：报告顶层领域 ID，例如 information 或 technology。
    输出：“按报告契约顺序返回现有栏目”得到的有序结构化记录；
      每项承载处理说明所定义的身份、证据或状态字段，可直接交给下一阶段。
    """
    sections = [section for section in report["sections"] if section.get("module") == module]
    if report.get("schema_version") not in {"1.3", "1.4", "1.5", "2.0"}:
        return sections
    by_id = {section["id"]: section for section in sections}
    return [by_id[section_id] for section_id in SECTION_GROUPS_V13[module] if section_id in by_id]


def group_items_by_source(
    section: dict[str, Any],
    language: object = "zh-CN",
) -> list[tuple[dict[str, str], list[dict]]]:
    """处理：按来源 ID 组织报告条目，供 Markdown 分区渲染。
    输入：
    - ``section``：报告中的栏目对象；包含栏目 ID、标题、简报或事件列表。
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：按“按来源 ID 组织报告条目，
      供 Markdown 分区渲染”规则得到的 ``tuple[dict[str, str`` 列表；
      列表顺序表达配置优先级、业务排名或稳定扫描顺序。
    """
    groups: dict[str, tuple[dict[str, str], list[dict]]] = {}
    values = section.get("briefs") if "briefs" in section else section.get("items", [])
    for item in values or []:
        source = item.get("primary_source") or {
            "id": "unknown",
            "name": localized(language, "未知来源", "Unknown source"),
            "url": (item.get("source_refs") or [item.get("source_ref", {})])[0]["url"],
        }
        key = str(source["id"])
        groups.setdefault(key, (source, []))[1].append(item)
    ordered = list(groups.values())
    for _source, items in ordered:
        items.sort(
            key=lambda value: (
                value["importance"],
                -int(value.get("source_rank", 1_000_000)),
            ),
            reverse=True,
        )
    ordered.sort(key=lambda group: group[1][0]["importance"], reverse=True)
    return ordered


def _image_markdown_url(image: dict[str, Any], media_path_prefix: str | None) -> str:
    """处理：把本地图片路径或安全公网地址转换为 Markdown URL。
    输入：
    - ``image``：报告或索引中的图片元数据；包含 URL、本地路径、哈希、尺寸和说明。
    - ``media_path_prefix``：HTML 或 Markdown 相对引用本地媒体文件时添加的路径前缀。
    输出：经过选择、规范化或安全处理的 URL 字符串，供后续访问或渲染使用。
    """
    local_path = str(image.get("local_path") or "").replace("\\", "/")
    local_parts = [part for part in local_path.split("/") if part]
    if (
        media_path_prefix
        and local_parts
        and local_parts[0] == "media"
        and ".." not in local_parts
    ):
        return f"{media_path_prefix.rstrip('/')}/{local_path}"
    return str(image.get("source_url") or image.get("url") or "")


def _brief_markdown(
    item: dict[str, Any],
    rank: int,
    media_path_prefix: str | None = None,
    language: object = "zh-CN",
) -> list[str]:
    """处理：把单条简报渲染为含来源和图片的 Markdown。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``rank``：简报或来源在当前栏目中的一基排序号。
    - ``media_path_prefix``：HTML 或 Markdown 相对引用本地媒体文件时添加的路径前缀。
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：“把单条简报渲染为含来源和图片的 Markdown”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    colon = "：" if is_chinese_output(language) else ":"
    ref = item["source_ref"]
    status_labels = STATUS_LABELS if is_chinese_output(language) else STATUS_LABELS_EN
    status = status_labels.get(item["status"], item["status"])
    source_rank = f" `{item['source_rank_label']}`" if item.get("source_rank_label") else ""
    lines: list[str] = []
    image = item.get("image")
    if isinstance(image, dict):
        image_url = _image_markdown_url(image, media_path_prefix)
        if image_url:
            lines.extend(
                [
                    f"![{image.get('caption', item['title'])}]({image_url})",
                    "",
                    f"*{localized(language, '图片来源', 'Image source')}{colon} "
                    f"{image.get('credit', localized(language, '原始来源', 'Original source'))}*",
                    "",
                ]
            )
    lines.append(
        f"**{rank}. [{item['title']}]({ref['url']})** `[{status}]`{source_rank}"
    )
    if localized_title := translated_title(item, language):
        lines.extend(
            [
                "",
                f"**{localized(language, '中文标题', 'English title')}{colon}** "
                f"{localized_title}",
            ]
        )
    if time_info := reference_time_label(ref, language):
        label, value = time_info
        lines.extend(["", f"**{label}{colon}** {value}"])
    lines.extend(["", f"**TL;DR{colon}** {item['tldr']}"])
    lines.append("")
    return lines


def _event_markdown(
    item: dict[str, Any],
    title: str,
    language: object = "zh-CN",
) -> list[str]:
    """处理：把单个事件渲染为兼容旧 schema 的 Markdown。
    输入：
    - ``item``：单个规范条目对象；通常包含 item_id、来源、标题、URL、时间和元数据。
    - ``title``：来源提供的标题文本；会清理空白，并用于过滤、身份或展示。
    - ``language``：规范语言标识；用于本地化选择或语言一致性判断。
    输出：“把单个事件渲染为兼容旧 schema 的 Markdown”得到的字符串列表；
      顺序保持确定并可供下一步骤逐项处理。
    """
    colon = "：" if is_chinese_output(language) else ":"
    separator = "，" if is_chinese_output(language) else ", "
    access_labels = ACCESS_LABELS if is_chinese_output(language) else ACCESS_LABELS_EN
    lines = [
        title,
        "",
        f"**{localized(language, '摘要（TL;DR）', 'Summary (TL;DR)')}{colon}** {item['tldr']}",
        "",
        f"**{localized(language, '为什么重要', 'Why it matters')}{colon}** "
        f"{item['why_it_matters']}",
        "",
        (
            f"**{localized(language, '重要性', 'Importance')}{colon}** "
            f"{item['importance']}/100 | "
            f"**{localized(language, '置信度', 'Confidence')}{colon}** "
            f"{item['confidence']:.2f}"
        ),
        "",
        f"**{localized(language, '重要性依据', 'Importance rationale')}{colon}** "
        f"{item['importance_reason']}",
        "",
        f"**{localized(language, '证据与原文', 'Evidence and sources')}{colon}**",
        "",
    ]
    for ref in item["source_refs"]:
        access = access_labels.get(ref["access"], ref["access"])
        time_text = ""
        if time_info := reference_time_label(ref, language):
            label, value = time_info
            time_text = f"{separator}{label}{colon}{value}"
        lines.append(
            f"- [{ref['title']}]({ref['url']}) — {access}{separator}"
            f"{ref['role']}{time_text}"
        )
    if item["evidence_notes"]:
        lines.extend(
            [
                "",
                f"**{localized(language, '证据说明', 'Evidence notes')}{colon}**",
                "",
            ]
        )
        lines.extend(f"- {note}" for note in item["evidence_notes"])
    image = item.get("image")
    if image:
        lines.extend(
            [
                "",
                f"![{image['caption']}]({image['url']})",
                "",
                f"*{localized(language, '图片来源', 'Image source')}{colon} "
                f"{image['credit']}*",
            ]
        )
    lines.append("")
    return lines


def render_report_markdown(
    report: dict[str, Any],
    media_path_prefix: str | None = None,
) -> str:
    """处理：把已验证报告栏目、来源引用和本地图片投影为可追踪的 Markdown。
    输入：
    - ``report``：当前报告结构；包含栏目、简报或事件、来源引用及质量元数据。
    - ``media_path_prefix``：HTML 或 Markdown 相对引用本地媒体文件时添加的路径前缀。
    输出：“把已验证报告栏目、来源引用和本地图片投影为可追踪的 Markdown”得到的规范字符串，
      供调用方存储、比较或展示。
    """
    language = report.get("language") or "zh-CN"
    chinese = is_chinese_output(language)
    colon = "：" if chinese else ":"
    joiner = "、" if chinese else ", "
    edition_labels = EDITION_LABELS if chinese else EDITION_LABELS_EN
    status_labels = STATUS_LABELS if chinese else STATUS_LABELS_EN
    domain_labels = DOMAIN_LABELS if chinese else DOMAIN_LABELS_EN
    state_labels = STATE_LABELS if chinese else STATE_LABELS_EN
    group_labels = GROUP_LABELS if chinese else GROUP_LABELS_EN
    assessment_labels = ASSESSMENT_LABELS if chinese else ASSESSMENT_LABELS_EN
    perspective_labels = PERSPECTIVE_LABELS if chinese else PERSPECTIVE_LABELS_EN
    analysis_section_labels = (
        ANALYSIS_SECTION_LABELS if chinese else ANALYSIS_SECTION_LABELS_EN
    )
    evaluation_labels = EVALUATION_LABELS if chinese else EVALUATION_LABELS_EN
    collection_notes_label = localized(
        language,
        "采集与验证说明",
        "Collection and verification notes",
    )
    stakeholder_positions_label = localized(
        language,
        "不同立场与利益",
        "Stakeholder positions and interests",
    )
    counterevidence_label = localized(
        language,
        "反证与不确定性",
        "Counterevidence and uncertainty",
    )
    changes_heading = localized(
        language,
        "日间新增、确认与修正",
        "New, Confirmed, and Revised Since Morning",
    )
    quality_heading = localized(
        language,
        "质量评估与用户反馈",
        "Quality Evaluation and Reader Feedback",
    )
    edition = edition_labels.get(report["edition"], report["edition"])
    lines = [
        f"# {report['title']}",
        "",
        f"- {localized(language, '版本', 'Edition')}{colon}{edition}",
        f"- {localized(language, '修订号', 'Revision')}{colon}{report['revision']}",
        f"- {localized(language, '生成时间', 'Generated at')}{colon}"
        f"{report['generated_at']}",
        "",
        f"**{localized(language, '摘要', 'Executive Summary')}**",
        "",
    ]
    lines.extend(f"- {item}" for item in report["executive_summary"])

    for module in ("information", "technology"):
        lines.extend(["", f"## {group_labels[module]}", ""])
        for section in ordered_sections(report, module):
            lines.extend([f"### {section['title']}", ""])
            if not section.get("items") and not section.get("briefs"):
                lines.extend([section["coverage_note"], ""])
            if report.get("schema_version") in BRIEF_REPORT_SCHEMAS:
                for source, items in group_items_by_source(section, language):
                    lines.extend([f"#### [{source['name']}]({source['url']})", ""])
                    for rank, item in enumerate(items, start=1):
                        lines.extend(
                            _brief_markdown(
                                item,
                                rank,
                                media_path_prefix,
                                language,
                            )
                        )
            elif report.get("schema_version") == "1.4":
                for source, items in group_items_by_source(section, language):
                    lines.extend([f"#### [{source['name']}]({source['url']})", ""])
                    for rank, item in enumerate(items, start=1):
                        link = item["source_refs"][0]["url"]
                        status = status_labels.get(item["status"], item["status"])
                        lines.extend(
                            _event_markdown(
                                item,
                                f"**{rank}. [{item['title']}]({link})** `[{status}]`",
                                language,
                            )
                        )
            else:
                for item in sorted(
                    section["items"],
                    key=lambda value: value["importance"],
                    reverse=True,
                ):
                    status = status_labels.get(item["status"], item["status"])
                    lines.extend(
                        _event_markdown(
                            item,
                            f"#### [{status}] {item['title']}",
                            language,
                        )
                    )
        if module == "information" and report["pending_verifications"]:
            lines.extend(
                [
                    f"**{collection_notes_label}{colon}**",
                    "",
                ]
            )
            for item in report["pending_verifications"]:
                lines.append(f"- {item['source_name']}: {item.get('note', item['status'])}")

    lines.extend(["", f"## {localized(language, '研判', 'Analysis')}", ""])
    if report["analyses"]:
        last_domain = None
        for analysis in report["analyses"]:
            domain = analysis.get("domain")
            if domain != last_domain:
                section_title = analysis_section_labels.get(
                    domain, domain_labels.get(domain, domain)
                )
                lines.extend(
                    [
                        f"### {section_title}",
                        "",
                    ]
                )
                last_domain = domain
            lines.extend(
                [
                    f"#### {analysis['claim']}",
                    "",
                    (
                        f"**{localized(language, '领域', 'Domain')}{colon}** "
                        f"{domain_labels.get(analysis['domain'], analysis['domain'])} | "
                        f"**{localized(language, '置信度', 'Confidence')}{colon}** "
                        f"{analysis['confidence']:.2f} | "
                        f"**{localized(language, '观点变化', 'State change')}{colon}** "
                        f"{state_labels.get(analysis['state_change'], analysis['state_change'])}"
                    ),
                    "",
                    f"**{localized(language, '证据事件', 'Evidence events')}{colon}** "
                    + ", ".join(analysis["evidence_event_ids"]),
                    "",
                    (
                        f"**{localized(language, '研判类型', 'Assessment types')}{colon}** "
                        + joiner.join(
                            assessment_labels.get(item, item)
                            for item in analysis.get("assessment_types", [])
                        )
                    ),
                    "",
                    (
                        f"**{localized(language, '观察视角', 'Perspectives')}{colon}** "
                        + joiner.join(
                            perspective_labels.get(item, item)
                            for item in analysis.get("perspectives", [])
                        )
                    ),
                    "",
                ]
            )
            for paragraph in split_narrative_paragraphs(analysis.get("narrative")):
                lines.extend([paragraph, ""])
            support_summary = localized(
                language,
                "论证与证据（展开）",
                "Evidence and reasoning (expand)",
            )
            lines.extend(
                [
                    "<details>",
                    f"<summary>{support_summary}</summary>",
                    "",
                    f"**{localized(language, '事实基础', 'Facts')}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis.get("facts", []))
            if analysis.get("historical_context"):
                lines.extend(
                    [
                        f"**{localized(language, '历史脉络', 'Historical context')}{colon}** "
                        f"{analysis['historical_context']}",
                        "",
                    ]
                )
            if analysis.get("dialectical_analysis"):
                lines.extend(
                    [
                        f"**{localized(language, '辩证分析', 'Dialectical analysis')}{colon}** "
                        f"{analysis['dialectical_analysis']}",
                        "",
                    ]
                )
            if analysis.get("stakeholder_positions"):
                lines.extend(
                    [
                        f"**{stakeholder_positions_label}{colon}**",
                        "",
                    ]
                )
                lines.extend(
                    f"- **{position['stakeholder']}{colon}** {position['position']} "
                    f"{localized(language, '利益基础', 'Interest basis')}{colon}"
                    f"{position['interests']}"
                    for position in analysis["stakeholder_positions"]
                )
            lines.extend(
                [
                    "",
                    f"**{localized(language, '推理链', 'Reasoning chain')}{colon}** "
                    f"{analysis.get('reasoning', '')}",
                    "",
                ]
            )
            lines.extend(
                [
                    f"**{counterevidence_label}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis["counter_evidence"])
            lines.extend(
                [
                    "",
                    f"**{localized(language, '可能情景', 'Scenarios')}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis.get("scenarios", []))
            lines.extend(
                [
                    "",
                    f"**{localized(language, '影响与启示', 'Implications')}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis["implications"])
            lines.extend(
                [
                    "",
                    f"**{localized(language, '建议行动', 'Recommended actions')}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis.get("actions", []))
            lines.extend(
                [
                    "",
                    f"**{localized(language, '后续观察信号', 'Watch signals')}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis["watch_signals"])
            lines.extend(
                [
                    "",
                    f"**{localized(language, '观点失效信号', 'Invalidation signals')}{colon}**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis.get("invalidation_signals", []))
            for title, key in (
                (localized(language, "因果传导链", "Causal chain"), "causal_chain"),
                (localized(language, "关键假设", "Key assumptions"), "assumptions"),
                (localized(language, "证据缺口", "Evidence gaps"), "evidence_gaps"),
            ):
                values = analysis.get(key, [])
                if values:
                    lines.extend(["", f"**{title}{colon}**", ""])
                    lines.extend(f"- {item}" for item in values)
            for title, key in (
                (localized(language, "时间跨度", "Time horizon"), "time_horizon"),
                (
                    localized(language, "置信度依据", "Confidence rationale"),
                    "confidence_rationale",
                ),
                (
                    localized(language, "相对上一版", "Change from prior"),
                    "change_from_prior",
                ),
                (
                    localized(language, "决策相关性", "Decision relevance"),
                    "decision_relevance",
                ),
            ):
                if analysis.get(key):
                    lines.extend(["", f"**{title}{colon}** {analysis[key]}"])
            lines.extend(["", "</details>"])
    else:
        lines.append(
            localized(
                language,
                "本版没有形成达到证据门槛的研判。",
                "No analysis met the evidence threshold in this edition.",
            )
        )

    synthesis = report.get("cross_perspective_synthesis")
    if isinstance(synthesis, dict):
        lines.extend(
            [
                "",
                f"### {localized(language, '跨视角综合', 'Cross-Perspective Synthesis')}",
                "",
                synthesis.get("overall_judgment", ""),
                "",
                f"**{localized(language, '共同结论', 'Consensus')}{colon}**",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in synthesis.get("consensus", []))
        lines.extend(
            [
                "",
                f"**{localized(language, '关键分歧', 'Key tensions')}{colon}**",
                "",
            ]
        )
        for tension in synthesis.get("tensions", []):
            if not isinstance(tension, dict):
                continue
            perspectives = joiner.join(tension.get("perspectives", []))
            lines.append(
                f"- **{tension.get('issue', '')}** ({perspectives}){colon}"
                f"{tension.get('source_of_difference', '')}"
            )
        for title, key in (
            (
                localized(
                    language,
                    "地缘—技术—市场传导链",
                    "Geopolitics–Technology–Markets Transmission Chain",
                ),
                "transmission_chain",
            ),
            (
                localized(language, "共同观察信号", "Shared watch signals"),
                "shared_watch_signals",
            ),
            (
                localized(language, "必须修正判断的触发条件", "Revision triggers"),
                "revision_triggers",
            ),
        ):
            lines.extend(["", f"**{title}{colon}**", ""])
            lines.extend(f"- {item}" for item in synthesis.get(key, []))
        lines.extend(
            [
                "",
                f"**{localized(language, '综合引用事件', 'Synthesis evidence events')}{colon}** "
                + joiner.join(synthesis.get("evidence_event_ids", [])),
            ]
        )

    if report["changes"]:
        lines.extend(
            [
                "",
                f"### {changes_heading}",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in report["changes"])

    if report.get("tomorrow_watch_items"):
        lines.extend(
            [
                "",
                f"### {localized(language, '次日观察项', 'Next-Day Watch List')}",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in report["tomorrow_watch_items"])

    evaluation = report.get("quality_evaluation")
    if evaluation:
        lines.extend(
            [
                "",
                f"## {quality_heading}",
                "",
                f"**{localized(language, '独立评估总分', 'Independent evaluation score')}"
                f"{colon}{evaluation['total_score']}/45**",
                "",
                localized(
                    language,
                    "| 维度 | 得分 | 重点结论 |",
                    "| Dimension | Score | Finding |",
                ),
                "| --- | ---: | --- |",
            ]
        )
        lines.extend(
            f"| {evaluation_labels.get(item['id'], item['id'])} | {item['score']}/5 | "
            f"{item['finding']} |"
            for item in evaluation["dimensions"]
        )
        for title, key in (
            (localized(language, "主要缺陷", "Main Defects"), "main_defects"),
            (
                localized(language, "证据不足项", "Insufficient Evidence"),
                "insufficient_evidence",
            ),
            (
                localized(language, "改进建议", "Recommended Improvements"),
                "improvements",
            ),
        ):
            lines.extend(["", f"### {title}", ""])
            values = evaluation.get(key, [])
            lines.extend(
                f"- {value}"
                for value in values
                or [localized(language, "无", "None")]
            )
        lines.extend(
            [
                "",
                f"### {localized(language, '用户反馈', 'Reader Feedback')}",
                "",
                f"- {localized(language, '相关性', 'Relevance')}{colon}__/5",
                f"- {localized(language, '准确性', 'Accuracy')}{colon}__/5",
                f"- {localized(language, '分析价值', 'Analysis value')}{colon}__/5",
                f"- {localized(language, '整体满意度', 'Overall satisfaction')}{colon}__/5",
                f"- {localized(language, '补充意见', 'Additional comments')}{colon}",
            ]
        )
    elif report.get("schema_version") in BRIEF_REPORT_SCHEMAS:
        lines.extend(
            [
                "",
                f"## {quality_heading}",
                "",
                localized(
                    language,
                    "独立评估将在日报发布后异步补充；评估意见不阻塞本版发布。",
                    "An independent evaluation will be added asynchronously after publication; "
                    "it does not block this edition.",
                ),
                "",
                f"### {localized(language, '用户反馈', 'Reader Feedback')}",
                "",
                f"- {localized(language, '相关性', 'Relevance')}{colon}__/5",
                f"- {localized(language, '准确性', 'Accuracy')}{colon}__/5",
                f"- {localized(language, '分析价值', 'Analysis value')}{colon}__/5",
                f"- {localized(language, '整体满意度', 'Overall satisfaction')}{colon}__/5",
                f"- {localized(language, '补充意见', 'Additional comments')}{colon}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def save_report(
    input_path: Path,
    index_path: Path,
    data_dir: Path,
    output_config: OutputConfig | None = None,
    media_config: MediaConfig | None = None,
    coverage_targets: dict[str, int] | None = None,
) -> dict[str, Any]:
    """处理：编译模型报告草稿、绑定权威索引、执行校验并创建不可变报告修订。
    输入：
    - ``input_path``：待编译的模型报告草稿 JSON 路径；其日期、版本和条目必须与索引一致。
    - ``index_path``：草稿所引用的不可变来源索引路径；用于恢复权威身份并验证证据。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``output_config``：本地 HTML、PDF、桌面交付和打开行为配置。
    - ``media_config``：图片下载、格式、安全、缓存和报告预算配置。
    - ``coverage_targets``：按来源 ID 指定的最小报告覆盖数；由运行情境拥有。
    输出：“编译模型报告草稿、绑定权威索引、执行校验并创建不可变报告修订”形成的结构化字典；
      典型键包括 compile_and_validation_seconds、content_hash、evaluation_status、json_path、loc
      al_output_error、local_output_seconds、markdown_path、media_seconds、persistence_seconds、
      report_id、save_metrics、semantic_cache_path。
    """
    save_started = time.perf_counter()
    raw = read_json(input_path)
    index = read_json(index_path)
    if not isinstance(raw, dict):
        raise ValueError("Report must be a JSON object")
    if not isinstance(index, dict):
        raise ValueError("Index must be a JSON object")

    report = deepcopy(raw)
    report.setdefault("date", index.get("date"))
    report.setdefault("edition", index.get("edition"))
    date = str(report["date"])
    edition = str(report["edition"])
    if date != str(index.get("date")) or edition != str(index.get("edition")):
        raise ValueError(
            "Report date and edition must match the source index: "
            f"report={date}/{edition}, "
            f"index={index.get('date')}/{index.get('edition')}"
        )
    report_dir = data_dir / "reports" / date
    revision = next_revision(report_dir, edition)
    report["revision"] = revision
    report["report_id"] = f"daily-{date}-{edition}-r{revision}"

    compile_started = time.perf_counter()
    # 先把模型草稿编译进确定性外壳，再统一规范化和校验。
    compile_warnings = compile_report_data(report, index, load_semantic_cache(data_dir))
    normalize_report_data(report, index)
    evaluation = report.get("quality_evaluation")
    if isinstance(evaluation, dict):
        evaluation["evaluated_report_id"] = report["report_id"]

    events_path = data_dir / "state" / "events.json"
    existing_events: list[dict[str, Any]] = []
    if events_path.exists():
        existing_payload = read_json(events_path)
        if isinstance(existing_payload, dict) and isinstance(existing_payload.get("items"), list):
            existing_events = [
                item for item in existing_payload["items"] if isinstance(item, dict)
            ]

    # 在触发网络图片处理前拒绝语义和来源身份错误，避免无效草稿产生外部副作用。
    errors, pre_media_validation_warnings = validate_report_data(
        report,
        index,
        existing_events,
        coverage_targets=coverage_targets,
    )
    if errors:
        raise ValueError("Report validation failed: " + "; ".join(errors))
    compile_validation_seconds = time.perf_counter() - compile_started

    media_started = time.perf_counter()
    media_warnings = materialize_report_images(
        report,
        index,
        data_dir,
        media_config or MediaConfig(),
    )
    media_seconds = time.perf_counter() - media_started
    errors, final_validation_warnings = validate_report_data(
        report,
        index,
        existing_events,
        coverage_targets=coverage_targets,
    )
    warnings = list(
        dict.fromkeys(
            [
                *compile_warnings,
                *media_warnings,
                *pre_media_validation_warnings,
                *final_validation_warnings,
            ]
        )
    )
    if errors:
        raise ValueError(
            "Report validation failed after media binding: " + "; ".join(errors)
        )

    persistence_started = time.perf_counter()
    json_path = report_dir / f"{edition}-r{revision}.json"
    markdown_path = report_dir / f"{edition}-r{revision}.md"
    # JSON 是权威记录，Markdown/HTML/PDF 都是可由它重新生成的投影。
    write_immutable_json(json_path, report)
    write_text_atomic(
        markdown_path,
        render_report_markdown(report, media_path_prefix="../.."),
    )
    write_json(data_dir / "reports" / f"latest-{edition}.json", report)
    persistence_seconds = time.perf_counter() - persistence_started

    local_output_started = time.perf_counter()
    try:
        local_outputs = write_local_outputs(
            report,
            data_dir,
            output_config or OutputConfig(),
        )
    except Exception as exc:
        warning = (
            "Local HTML/PDF projection failed after JSON/Markdown persistence: "
            f"{type(exc).__name__}: {exc}"
        )
        local_outputs = {"local_output_error": warning, "warnings": [warning]}
    local_output_seconds = time.perf_counter() - local_output_started
    warnings.extend(local_outputs.pop("warnings", []))

    state_started = time.perf_counter()
    semantic_cache_path = None
    state_paths: dict[str, Any] = {}
    try:
        # 报告已经安全持久化；派生语义/连续性状态失败只降级为警告，不回滚记录。
        semantic_cache_path = update_semantic_cache_from_report(report, index, data_dir)
        state_paths = (
            update_continuity_state(report, data_dir)
            if (
                report.get("quality_evaluation")
                or report.get("schema_version") not in BRIEF_REPORT_SCHEMAS
            )
            else {}
        )
    except Exception as exc:
        warnings.append(
            "Derived semantic/continuity state update failed after report persistence: "
            f"{type(exc).__name__}: {exc}"
        )
    state_seconds = time.perf_counter() - state_started
    save_metrics = {
        "compile_and_validation_seconds": round(compile_validation_seconds, 3),
        "media_seconds": round(media_seconds, 3),
        "persistence_seconds": round(persistence_seconds, 3),
        "local_output_seconds": round(local_output_seconds, 3),
        "state_update_seconds": round(state_seconds, 3),
        "total_seconds": round(time.perf_counter() - save_started, 3),
    }

    return {
        "report_id": report["report_id"],
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        **local_outputs,
        "warnings": warnings,
        "state_paths": state_paths,
        "content_hash": report_content_hash(report),
        "evaluation_status": report.get("evaluation_status", "completed"),
        **(
            {"semantic_cache_path": str(semantic_cache_path)}
            if semantic_cache_path is not None
            else {}
        ),
        "save_metrics": save_metrics,
    }


def save_evaluation(
    input_path: Path,
    report_path: Path,
    data_dir: Path,
    output_config: OutputConfig | None = None,
) -> dict[str, Any]:
    """处理：校验独立评估与报告身份和内容哈希一致后，创建不可变评估修订。
    输入：
    - ``input_path``：上游阶段生成的输入文件路径；读取前会执行存在性或数据根校验。
    - ``report_path``：版本化报告 JSON 路径；本地报告是 HTML、PDF 和 Notion 的事实源。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``output_config``：本地 HTML、PDF、桌面交付和打开行为配置。
    输出：“校验独立评估与报告身份和内容哈希一致后，创建不可变评估修订”形成的结构化字典；
      典型键包括 content_hash、evaluation_id、evaluation_path、local_outputs、semantic_cache_pat
      h、state_paths、status。
    """
    raw = read_json(input_path)
    report = read_json(report_path)
    if not isinstance(raw, dict) or not isinstance(report, dict):
        raise ValueError("Evaluation and report must both be JSON objects")
    evaluation = dict(raw)
    date = str(report["date"])
    edition = str(report["edition"])
    evaluation_dir = data_dir / "evaluations" / date
    revision = next_revision(evaluation_dir, edition)
    evaluation["evaluation_id"] = f"evaluation-{report['report_id']}-r{revision}"
    evaluation.setdefault("evaluated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    errors = validate_evaluation_data(evaluation, report)
    if errors:
        raise ValueError("Evaluation validation failed: " + "; ".join(errors))
    output = evaluation_dir / f"{edition}-r{revision}.json"
    write_immutable_json(output, evaluation)
    write_json(data_dir / "evaluations" / f"latest-{edition}.json", evaluation)
    semantic_cache_path = finalize_semantic_cache_evaluation(evaluation, data_dir)
    assessed_report = dict(report)
    assessed_report["quality_evaluation"] = evaluation
    state_paths = update_continuity_state(assessed_report, data_dir)
    local_outputs = write_local_outputs(
        report,
        data_dir,
        output_config or OutputConfig(),
        evaluation=evaluation,
        open_after_finalize=False,
    )
    run_path = data_dir / "runs" / date / f"{edition}.json"
    if run_path.exists():
        run = read_json(run_path)
        if isinstance(run, dict) and run.get("artifacts", {}).get("report_id") == report.get(
            "report_id"
        ):
            run["evaluation"] = {
                "status": "completed",
                "evaluation_id": evaluation["evaluation_id"],
                "evaluation_path": str(output),
                "content_hash": report_content_hash(report),
            }
            run.setdefault("artifacts", {}).update(
                {key: value for key, value in local_outputs.items() if key.endswith("_path")}
            )
            write_json(run_path, run)
    return {
        "evaluation_id": evaluation["evaluation_id"],
        "evaluation_path": str(output),
        "content_hash": report_content_hash(report),
        "state_paths": state_paths,
        "local_outputs": local_outputs,
        **({"semantic_cache_path": str(semantic_cache_path)} if semantic_cache_path else {}),
    }
