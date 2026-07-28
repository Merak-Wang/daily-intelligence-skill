# ruff: noqa: E501
from __future__ import annotations

import base64
import html
import json
import os
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import OutputConfig, validate_output_config
from .localization import (
    is_chinese_output,
    localized,
    translated_title,
)
from .reporting import reference_time_label, split_narrative_paragraphs
from .storage import write_text_atomic
from .taxonomy import SECTION_GROUPS_V13
from .utils import read_json

EDITION_LABELS = {"morning": "晨间版", "evening": "晚间版"}
EDITION_LABELS_EN = {"morning": "Morning Edition", "evening": "Evening Edition"}
MODULE_LABELS = {"information": "资讯", "technology": "技术"}
MODULE_LABELS_EN = {"information": "News", "technology": "Technology"}
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
ANALYSIS_LABELS = {
    "geopolitics": "从地缘政治专家的角度",
    "ai_technology": "从 AI 研究/开发工程师的角度",
    "markets": "从股票分析师的角度",
}
ANALYSIS_LABELS_EN = {
    "geopolitics": "Geopolitical Perspective",
    "ai_technology": "AI Research and Engineering Perspective",
    "markets": "Equity-Market Perspective",
}
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

UI_LABELS = {
    "zh-CN": {
        "none": "无",
        "unknown_source": "未知来源",
        "items": "条",
        "facts": "事实基础",
        "narrative": "综合论述",
        "history": "历史脉络",
        "dialectic": "辩证分析",
        "reasoning": "推理链",
        "counter": "反证与不确定性",
        "scenarios": "可能情景",
        "implications": "影响与启示",
        "actions": "建议行动",
        "watch": "后续观察信号",
        "invalidation": "观点失效信号",
        "causal": "因果传导链",
        "assumptions": "关键假设",
        "gaps": "证据缺口",
        "horizon": "时间跨度",
        "confidence_basis": "置信度依据",
        "change_prior": "相对上一版",
        "decision": "决策相关性",
        "support": "论证与证据（展开）",
        "stakeholders": "不同立场与利益",
        "interest_basis": "利益基础",
        "confidence": "置信度",
        "evidence": "证据",
        "unbound": "未绑定",
        "consensus": "共同结论",
        "tensions": "关键分歧",
        "transmission": "地缘—技术—市场传导链",
        "shared_watch": "共同观察信号",
        "revision_triggers": "修正判断的触发条件",
        "synthesis": "跨视角综合",
        "summary": "今日摘要",
        "analysis": "研判",
        "evaluation": "质量评估",
        "feedback": "用户反馈",
        "toc": "目录",
        "report_toc": "报告目录",
        "toc_heading": "定位目录",
        "collapse": "收起",
        "evaluation_pending": "独立评估处理中",
        "evaluation_pending_detail": "日报已经交付，评估 Agent 将异步补充九维评分与修改意见。",
        "main_defects": "主要缺陷",
        "insufficient_evidence": "证据不足项",
        "improvements": "改进建议",
        "dimension": "维度",
        "score": "得分",
        "finding": "重点结论",
        "empty": "本时段暂无可发布内容。",
        "pending_sources": "待验证来源",
        "analysis_empty": "本版没有形成达到证据门槛的该领域研判。",
        "changes": "日间新增、确认与修正",
        "next_watch": "次日观察项",
        "archive": "日报中心",
        "revision": "修订",
        "filter": "筛选标题、摘要或来源",
        "unrated": "未评分",
        "relevance": "相关性",
        "accuracy": "准确性",
        "analysis_value": "分析价值",
        "satisfaction": "整体满意度",
        "comments": "补充意见",
        "feedback_placeholder": "这些反馈可作为后续日报个性化输入。",
        "download_feedback": "下载反馈 JSON",
        "feedback_note": "本地文件不会自动上传数据。请把下载的 JSON 交给 Hermes，作为下一版的人工反馈输入。",
        "footer": "本地 JSON/Markdown 为事实源；HTML/PDF 是可重新生成的阅读投影。",
        "page": "第 {page} 页",
        "evaluation_and_feedback": "质量评估与用户反馈",
        "evaluation_total": "独立评估总分",
        "delivery_without_score": "独立评估处理中，日报交付不等待评分。",
        "image_source": "图片来源",
    },
    "en": {
        "none": "None",
        "unknown_source": "Unknown source",
        "items": "items",
        "facts": "Facts",
        "narrative": "Narrative",
        "history": "Historical Context",
        "dialectic": "Dialectical Analysis",
        "reasoning": "Reasoning Chain",
        "counter": "Counterevidence and Uncertainty",
        "scenarios": "Scenarios",
        "implications": "Implications",
        "actions": "Recommended Actions",
        "watch": "Watch Signals",
        "invalidation": "Invalidation Signals",
        "causal": "Causal Chain",
        "assumptions": "Key Assumptions",
        "gaps": "Evidence Gaps",
        "horizon": "Time Horizon",
        "confidence_basis": "Confidence Rationale",
        "change_prior": "Change From Prior",
        "decision": "Decision Relevance",
        "support": "Evidence and reasoning (expand)",
        "stakeholders": "Stakeholder Positions and Interests",
        "interest_basis": "Interest basis",
        "confidence": "Confidence",
        "evidence": "Evidence",
        "unbound": "Unbound",
        "consensus": "Consensus",
        "tensions": "Key Tensions",
        "transmission": "Geopolitics–Technology–Markets Transmission Chain",
        "shared_watch": "Shared Watch Signals",
        "revision_triggers": "Revision Triggers",
        "synthesis": "Cross-Perspective Synthesis",
        "summary": "Executive Summary",
        "analysis": "Analysis",
        "evaluation": "Quality Evaluation",
        "feedback": "Reader Feedback",
        "toc": "Contents",
        "report_toc": "Report contents",
        "toc_heading": "Navigate",
        "collapse": "Close",
        "evaluation_pending": "Independent evaluation pending",
        "evaluation_pending_detail": "The report is ready; a separate evaluator will add nine-dimension scores and recommendations.",
        "main_defects": "Main Defects",
        "insufficient_evidence": "Insufficient Evidence",
        "improvements": "Recommended Improvements",
        "dimension": "Dimension",
        "score": "Score",
        "finding": "Finding",
        "empty": "No publishable items were available in this window.",
        "pending_sources": "Sources Pending Verification",
        "analysis_empty": "No analysis in this domain met the evidence threshold.",
        "changes": "New, Confirmed, and Revised Since Morning",
        "next_watch": "Next-Day Watch List",
        "archive": "Report Archive",
        "revision": "Revision",
        "filter": "Filter titles, summaries, or sources",
        "unrated": "Not rated",
        "relevance": "Relevance",
        "accuracy": "Accuracy",
        "analysis_value": "Analysis value",
        "satisfaction": "Overall satisfaction",
        "comments": "Additional comments",
        "feedback_placeholder": "This feedback can guide later editions.",
        "download_feedback": "Download feedback JSON",
        "feedback_note": "This local file does not upload data. Give the downloaded JSON to Hermes as feedback for the next edition.",
        "footer": "Local JSON and Markdown are the source of truth; HTML and PDF are reproducible reading views.",
        "page": "Page {page}",
        "evaluation_and_feedback": "Quality Evaluation and Reader Feedback",
        "evaluation_total": "Independent evaluation score",
        "delivery_without_score": "Independent evaluation is pending; report delivery does not wait for scoring.",
        "image_source": "Image source",
    },
}


def _ui(language: object) -> dict[str, str]:
    return UI_LABELS["zh-CN" if is_chinese_output(language) else "en"]


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _safe_url(value: object) -> str:
    url = str(value or "")
    parsed = urlsplit(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return _escape(url)
    return "#"


_EMBEDDABLE_IMAGE_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
_MAX_EMBEDDED_IMAGE_BYTES = 8 * 1024 * 1024


def _validated_local_image_path(
    image: dict[str, Any],
    data_dir: Path,
) -> Path | None:
    local_path = str(image.get("local_path") or "").replace("\\", "/")
    parts = [part for part in local_path.split("/") if part]
    content_type = str(image.get("content_type") or "").lower()
    if (
        len(parts) < 3
        or parts[:2] != ["media", "images"]
        or ".." in parts
        or content_type not in _EMBEDDABLE_IMAGE_TYPES
    ):
        return None
    image_root = (data_dir / "media" / "images").resolve()
    candidate = data_dir.joinpath(*parts).resolve()
    try:
        candidate.relative_to(image_root)
        size = candidate.stat().st_size
    except (OSError, ValueError):
        return None
    if not candidate.is_file() or size <= 0 or size > _MAX_EMBEDDED_IMAGE_BYTES:
        return None
    return candidate


def _image_src(
    image: dict[str, Any],
    media_path_prefix: str | None,
    embedded_image_sources: dict[str, str] | None = None,
) -> str:
    local_path = str(image.get("local_path") or "").replace("\\", "/")
    if embedded_image_sources and local_path in embedded_image_sources:
        return _escape(embedded_image_sources[local_path])
    local_parts = [part for part in local_path.split("/") if part]
    if (
        media_path_prefix
        and local_parts
        and local_parts[0] == "media"
        and ".." not in local_parts
    ):
        return _escape(f"{media_path_prefix.rstrip('/')}/{local_path}")
    return _safe_url(image.get("source_url") or image.get("url"))


def _embedded_image_sources(
    report: dict[str, Any],
    data_dir: Path,
) -> dict[str, str]:
    embedded: dict[str, str] = {}
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        items = section.get("briefs") if "briefs" in section else section.get("items", [])
        for item in items or []:
            image = item.get("image") if isinstance(item, dict) else None
            if not isinstance(image, dict):
                continue
            local_path = str(image.get("local_path") or "").replace("\\", "/")
            content_type = str(image.get("content_type") or "").lower()
            if local_path in embedded:
                continue
            candidate = _validated_local_image_path(image, data_dir)
            if candidate is None:
                continue
            try:
                payload = candidate.read_bytes()
            except OSError:
                continue
            encoded = base64.b64encode(payload).decode("ascii")
            embedded[local_path] = f"data:{content_type};base64,{encoded}"
    return embedded


def _external_link(label: object, url: object, *, css_class: str = "") -> str:
    class_attr = f' class="{_escape(css_class)}"' if css_class else ""
    return (
        f'<a{class_attr} href="{_safe_url(url)}" target="_blank" '
        f'rel="noopener noreferrer">{_escape(label)}</a>'
    )


def _ordered_sections(report: dict[str, Any], module: str) -> list[dict[str, Any]]:
    sections = [section for section in report.get("sections", []) if section.get("module") == module]
    by_id = {str(section.get("id")): section for section in sections}
    ordered = [by_id[key] for key in SECTION_GROUPS_V13[module] if key in by_id]
    ordered.extend(section for section in sections if section not in ordered)
    return ordered


def _group_items(
    section: dict[str, Any],
    language: object = "zh-CN",
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    values = section.get("briefs") if "briefs" in section else section.get("items", [])
    groups: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for item in values or []:
        source = item.get("primary_source")
        if not isinstance(source, dict):
            refs = item.get("source_refs") or [item.get("source_ref") or {}]
            source = {
                "id": "unknown",
                "name": _ui(language)["unknown_source"],
                "url": refs[0].get("url", "#"),
            }
        key = str(source.get("id") or source.get("name") or "unknown")
        groups.setdefault(key, (source, []))[1].append(item)
    ordered = list(groups.values())
    for _source, items in ordered:
        items.sort(
            key=lambda value: (
                int(value.get("importance", 0)),
                -int(value.get("source_rank", 1_000_000)),
            ),
            reverse=True,
        )
    ordered.sort(
        key=lambda group: int(group[1][0].get("importance", 0)) if group[1] else 0,
        reverse=True,
    )
    return ordered


def _list_html(
    values: list[object],
    *,
    css_class: str = "",
    language: object = "zh-CN",
) -> str:
    if not values:
        return f'<p class="muted">{_escape(_ui(language)["none"])}</p>'
    class_attr = f' class="{_escape(css_class)}"' if css_class else ""
    return f"<ul{class_attr}>" + "".join(f"<li>{_escape(value)}</li>" for value in values) + "</ul>"


def _prose_html(value: object, *, css_class: str = "") -> str:
    class_attr = f' class="{_escape(css_class)}"' if css_class else ""
    paragraphs = split_narrative_paragraphs(value)
    return f"<div{class_attr}>" + "".join(
        f"<p>{_escape(paragraph)}</p>" for paragraph in paragraphs
    ) + "</div>"


def _item_ref(item: dict[str, Any]) -> dict[str, Any]:
    ref = item.get("source_ref")
    if isinstance(ref, dict):
        return ref
    refs = item.get("source_refs") or []
    return refs[0] if refs and isinstance(refs[0], dict) else {}


def _brief_html(
    item: dict[str, Any],
    rank: int,
    media_path_prefix: str | None = None,
    embedded_image_sources: dict[str, str] | None = None,
    language: object = "zh-CN",
) -> str:
    ref = _item_ref(item)
    status_labels = STATUS_LABELS if is_chinese_output(language) else STATUS_LABELS_EN
    status = status_labels.get(str(item.get("status")), str(item.get("status") or ""))
    source_rank = item.get("source_rank_label")
    event_id = item.get("featured_event_id") or item.get("event_id")
    anchor = f' id="event-{_escape(event_id)}"' if event_id else ""
    badges = [f'<span class="badge status">{_escape(status)}</span>'] if status else []
    if source_rank:
        badges.append(f'<span class="badge rank">{_escape(source_rank)}</span>')
    title = _external_link(
        item.get("title", localized(language, "无标题", "Untitled")),
        ref.get("url"),
        css_class="story-link",
    )
    localized_title = translated_title(item, language)
    translation = (
        f'<p class="translated-title">{_escape(localized_title)}</p>'
        if localized_title
        else ""
    )
    time_html = ""
    if time_info := reference_time_label(ref, language):
        label, value = time_info
        time_html = f'<p class="story-time">{_escape(label)}：{_escape(value)}</p>'
    image = item.get("image")
    figure = ""
    if isinstance(image, dict) and (
        image.get("local_path") or image.get("source_url") or image.get("url")
    ):
        figure = (
            '<figure><img loading="lazy" referrerpolicy="no-referrer" '
            f'src="{_image_src(image, media_path_prefix, embedded_image_sources)}" '
            f'alt="{_escape(image.get("caption"))}">'
            f'<figcaption>{_escape(image.get("caption"))} · '
            f'{_escape(image.get("credit"))}</figcaption></figure>'
        )
    image_class = " has-image" if figure else ""
    return (
        f'<article class="brief{image_class}"{anchor} '
        f'data-search="{_escape(item.get("title"))} '
        f'{_escape(localized_title)} {_escape(item.get("tldr"))}">'
        '<div class="brief-heading">'
        f'<span class="ordinal">{rank:02d}</span><div><h4>{title}</h4>{translation}{time_html}</div>'
        f'<div class="badges">{"".join(badges)}</div></div>'
        f'{figure}<p class="tldr"><span>TL;DR</span>'
        f'{_escape(item.get("tldr"))}</p></article>'
    )


def _source_section_html(
    source: dict[str, Any],
    items: list[dict[str, Any]],
    media_path_prefix: str | None = None,
    embedded_image_sources: dict[str, str] | None = None,
    language: object = "zh-CN",
) -> str:
    source_name = source.get("name") or _ui(language)["unknown_source"]
    stories = "".join(
        _brief_html(
            item,
            rank,
            media_path_prefix,
            embedded_image_sources,
            language,
        )
        for rank, item in enumerate(items, start=1)
    )
    return (
        f'<section class="source-group" data-search="{_escape(source_name)}">'
        '<div class="source-heading">'
        f'<h3>{_external_link(source_name, source.get("url"))}</h3>'
        f'<span>{len(items)} {_escape(_ui(language)["items"])}</span></div>'
        f"{stories}</section>"
    )


def _analysis_html(
    analysis: dict[str, Any],
    language: object = "zh-CN",
) -> str:
    labels = _ui(language)
    event_links = []
    for event_id in analysis.get("evidence_event_ids", []):
        event_links.append(f'<a href="#event-{_escape(event_id)}">{_escape(event_id)}</a>')
    stakeholder_rows = "".join(
        '<div class="stakeholder"><strong>'
        f'{_escape(row.get("stakeholder"))}</strong><p>{_escape(row.get("position"))}</p>'
        f'<small>{_escape(labels["interest_basis"])}: '
        f'{_escape(row.get("interests"))}</small></div>'
        for row in analysis.get("stakeholder_positions", [])
        if isinstance(row, dict)
    )
    sections = [
        (labels["facts"], _list_html(analysis.get("facts", []), language=language)),
        (labels["history"], f'<p>{_escape(analysis.get("historical_context"))}</p>'),
        (labels["dialectic"], f'<p>{_escape(analysis.get("dialectical_analysis"))}</p>'),
        (labels["reasoning"], f'<p>{_escape(analysis.get("reasoning"))}</p>'),
        (
            labels["counter"],
            _list_html(analysis.get("counter_evidence", []), language=language),
        ),
        (labels["scenarios"], _list_html(analysis.get("scenarios", []), language=language)),
        (
            labels["implications"],
            _list_html(analysis.get("implications", []), language=language),
        ),
        (labels["actions"], _list_html(analysis.get("actions", []), language=language)),
        (labels["watch"], _list_html(analysis.get("watch_signals", []), language=language)),
        (
            labels["invalidation"],
            _list_html(analysis.get("invalidation_signals", []), language=language),
        ),
        (labels["causal"], _list_html(analysis.get("causal_chain", []), language=language)),
        (
            labels["assumptions"],
            _list_html(analysis.get("assumptions", []), language=language),
        ),
        (labels["gaps"], _list_html(analysis.get("evidence_gaps", []), language=language)),
        (labels["horizon"], f'<p>{_escape(analysis.get("time_horizon"))}</p>'),
        (
            labels["confidence_basis"],
            f'<p>{_escape(analysis.get("confidence_rationale"))}</p>',
        ),
        (labels["change_prior"], f'<p>{_escape(analysis.get("change_from_prior"))}</p>'),
        (labels["decision"], f'<p>{_escape(analysis.get("decision_relevance"))}</p>'),
    ]
    body = "".join(
        f'<section class="analysis-part"><h5>{title}</h5>{content}</section>'
        for title, content in sections
        if content not in {'<p></p>', '<p class="muted">无</p>'}
    )
    if stakeholder_rows:
        body += (
            f'<section class="analysis-part"><h5>{_escape(labels["stakeholders"])}</h5>'
            f'<div class="stakeholder-grid">{stakeholder_rows}</div></section>'
        )
    narrative = _prose_html(
        analysis.get("narrative"),
        css_class="analysis-narrative",
    )
    confidence = analysis.get("confidence")
    confidence_text = f"{float(confidence):.0%}" if isinstance(confidence, (int, float)) else "-"
    return (
        '<article class="analysis-card">'
        f'<h4>{_escape(analysis.get("claim"))}</h4>'
        '<div class="analysis-meta">'
        f'<span>{_escape(labels["confidence"])} {confidence_text}</span>'
        f'<span>{_escape(labels["evidence"])} '
        f'{" · ".join(event_links) or _escape(labels["unbound"])}</span></div>'
        f"{narrative}"
        '<details class="analysis-notebook">'
        f'<summary>{_escape(labels["support"])}</summary>'
        f'<div class="analysis-notebook-body">{body}</div></details></article>'
    )


def _synthesis_html(
    synthesis: dict[str, Any] | None,
    language: object = "zh-CN",
) -> str:
    if not isinstance(synthesis, dict):
        return ""
    labels = _ui(language)
    separator = "、" if is_chinese_output(language) else ", "
    tensions = "".join(
        "<li><strong>"
        f"{_escape(row.get('issue'))}</strong> ("
        f"{_escape(separator.join(row.get('perspectives', [])))}): "
        f"{_escape(row.get('source_of_difference'))}</li>"
        for row in synthesis.get("tensions", [])
        if isinstance(row, dict)
    )
    sections = [
        (
            labels["consensus"],
            _list_html(synthesis.get("consensus", []), language=language),
        ),
        (labels["tensions"], f"<ul>{tensions}</ul>" if tensions else ""),
        (
            labels["transmission"],
            _list_html(synthesis.get("transmission_chain", []), language=language),
        ),
        (
            labels["shared_watch"],
            _list_html(synthesis.get("shared_watch_signals", []), language=language),
        ),
        (
            labels["revision_triggers"],
            _list_html(synthesis.get("revision_triggers", []), language=language),
        ),
    ]
    body = "".join(
        f'<section class="analysis-part"><h5>{title}</h5>{content}</section>'
        for title, content in sections
        if content and content != '<p class="muted">无</p>'
    )
    evidence = " · ".join(
        _escape(value) for value in synthesis.get("evidence_event_ids", [])
    )
    return (
        '<section class="analysis-domain synthesis" id="analysis-synthesis">'
        f'<h3>{_escape(labels["synthesis"])}</h3>'
        '<article class="analysis-card synthesis-card">'
        f'<h4>{_escape(synthesis.get("overall_judgment"))}</h4>'
        f'<div class="analysis-meta"><span>{_escape(labels["evidence"])} '
        f'{evidence or _escape(labels["unbound"])}</span></div>'
        f"{body}</article></section>"
    )


def _toc_html(report: dict[str, Any]) -> str:
    language = report.get("language") or "zh-CN"
    labels = _ui(language)
    modules = MODULE_LABELS if is_chinese_output(language) else MODULE_LABELS_EN
    analyses = ANALYSIS_LABELS if is_chinese_output(language) else ANALYSIS_LABELS_EN
    entries: list[tuple[str, str, int]] = [("summary", labels["summary"], 0)]
    for module in ("information", "technology"):
        entries.append((f"module-{module}", modules[module], 0))
        entries.extend(
            (
                str(section.get("id") or ""),
                str(section.get("title") or section.get("id") or ""),
                1,
            )
            for section in _ordered_sections(report, module)
            if section.get("id")
        )
    entries.append(("analysis", labels["analysis"], 0))
    entries.extend(
        (f"analysis-{domain}", label, 1)
        for domain, label in analyses.items()
    )
    if isinstance(report.get("cross_perspective_synthesis"), dict):
        entries.append(("analysis-synthesis", labels["synthesis"], 1))
    entries.extend(
        (
            ("evaluation", labels["evaluation"], 0),
            ("feedback", labels["feedback"], 0),
        )
    )
    links = "".join(
        f'<a class="toc-link toc-level-{level}" href="#{_escape(anchor)}">'
        f"{_escape(label)}</a>"
        for anchor, label, level in entries
    )
    return (
        '<button class="toc-toggle" id="toc-toggle" type="button" '
        'aria-controls="report-toc" aria-expanded="false">'
        f'<span aria-hidden="true">☰</span><span>{_escape(labels["toc"])}</span></button>'
        '<div class="toc-scrim" id="toc-scrim" aria-hidden="true"></div>'
        f'<aside class="report-toc" id="report-toc" '
        f'aria-label="{_escape(labels["report_toc"])}" aria-hidden="true">'
        f'<div class="toc-heading"><strong>{_escape(labels["toc_heading"])}</strong>'
        f'<button class="toc-close" id="toc-close" type="button" '
        f'aria-label="{_escape(labels["collapse"])}">'
        f'{_escape(labels["collapse"])}</button></div>'
        f'<nav class="toc-nav">{links}</nav></aside>'
    )


def _evaluation_html(
    evaluation: dict[str, Any] | None,
    language: object = "zh-CN",
) -> str:
    labels = _ui(language)
    evaluation_labels = (
        EVALUATION_LABELS if is_chinese_output(language) else EVALUATION_LABELS_EN
    )
    if not evaluation:
        return (
            f'<div class="evaluation-pending"><strong>'
            f'{_escape(labels["evaluation_pending"])}</strong>'
            f'<p>{_escape(labels["evaluation_pending_detail"])}</p></div>'
        )
    dimensions = "".join(
        '<tr><td>'
        f'{_escape(evaluation_labels.get(str(row.get("id")), row.get("id")))}</td>'
        f'<td><span class="score">{_escape(row.get("score"))}/5</span></td>'
        f'<td>{_escape(row.get("finding"))}</td></tr>'
        for row in evaluation.get("dimensions", [])
        if isinstance(row, dict)
    )
    notes = "".join(
        f'<section><h4>{title}</h4>{_list_html(evaluation.get(key, []))}</section>'
        for title, key in (
            (labels["main_defects"], "main_defects"),
            (labels["insufficient_evidence"], "insufficient_evidence"),
            (labels["improvements"], "improvements"),
        )
    )
    return (
        '<div class="evaluation-score">'
        f'<strong>{_escape(evaluation.get("total_score"))}</strong><span>/ 45</span></div>'
        f'<div class="table-wrap"><table><thead><tr><th>{_escape(labels["dimension"])}</th>'
        f'<th>{_escape(labels["score"])}</th>'
        f'<th>{_escape(labels["finding"])}</th></tr></thead>'
        f"<tbody>{dimensions}</tbody></table></div>{notes}"
    )


def render_report_html(
    report: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    *,
    include_pdf_link: bool = True,
    media_path_prefix: str | None = None,
    embedded_image_sources: dict[str, str] | None = None,
    archive_href: str | None = "../index.html",
    pdf_href: str | None = None,
) -> str:
    language = report.get("language") or "zh-CN"
    labels = _ui(language)
    module_labels = MODULE_LABELS if is_chinese_output(language) else MODULE_LABELS_EN
    edition_labels = EDITION_LABELS if is_chinese_output(language) else EDITION_LABELS_EN
    analysis_labels = (
        ANALYSIS_LABELS if is_chinese_output(language) else ANALYSIS_LABELS_EN
    )
    evaluation = evaluation or (
        report.get("quality_evaluation")
        if isinstance(report.get("quality_evaluation"), dict)
        else None
    )
    module_blocks = []
    for module in ("information", "technology"):
        section_blocks = []
        for section in _ordered_sections(report, module):
            source_groups = "".join(
                _source_section_html(
                    source,
                    items,
                    media_path_prefix,
                    embedded_image_sources,
                    language,
                )
                for source, items in _group_items(section, language)
            )
            empty_note = ""
            if not source_groups:
                empty_note = (
                    '<div class="empty-note">'
                    f'{_escape(section.get("coverage_note") or labels["empty"])}</div>'
                )
            section_blocks.append(
                f'<section class="content-section" id="{_escape(section.get("id"))}">'
                f'<h2>{_escape(section.get("title"))}</h2>{source_groups}{empty_note}</section>'
            )
        module_blocks.append(
            f'<section class="module" id="module-{module}"><div class="module-label">'
            f'{module_labels[module]}</div>{"".join(section_blocks)}</section>'
        )

    pending = "".join(
        '<li>'
        f'{_external_link(item.get("source_name"), item.get("url"))}'
        f'<span>{_escape(item.get("note") or item.get("status"))}</span></li>'
        for item in report.get("pending_verifications", [])
        if isinstance(item, dict)
    )
    pending_block = (
        f'<aside class="pending"><h3>{_escape(labels["pending_sources"])}</h3><ul>'
        + pending
        + "</ul></aside>"
        if pending
        else ""
    )

    analysis_groups = []
    for domain in ("geopolitics", "ai_technology", "markets"):
        analyses = [row for row in report.get("analyses", []) if row.get("domain") == domain]
        cards = "".join(_analysis_html(row, language) for row in analyses)
        if not cards:
            cards = f'<div class="empty-note">{_escape(labels["analysis_empty"])}</div>'
        analysis_groups.append(
            f'<section class="analysis-domain" id="analysis-{domain}">'
            f"<h3>{analysis_labels[domain]}</h3>{cards}</section>"
        )
    synthesis_block = _synthesis_html(
        report.get("cross_perspective_synthesis"), language
    )

    changes = report.get("changes", [])
    changes_block = (
        f'<section class="follow-up"><h3>{_escape(labels["changes"])}</h3>'
        f'{_list_html(changes, language=language)}</section>'
        if changes
        else ""
    )
    watch = report.get("tomorrow_watch_items", [])
    watch_block = (
        f'<section class="follow-up"><h3>{_escape(labels["next_watch"])}</h3>'
        f'{_list_html(watch, language=language)}</section>'
        if watch
        else ""
    )
    report_id = str(report.get("report_id") or "daily-intelligence")
    feedback_data = json.dumps(
        {"report_id": report_id, "date": report.get("date"), "edition": report.get("edition")},
        ensure_ascii=False,
    ).replace("</", "<\\/")
    pdf_link = ""
    if include_pdf_link:
        resolved_pdf_href = pdf_href or (
            f"{report.get('edition')}-r{report.get('revision')}.pdf"
        )
        pdf_link = (
            f'<a href="{_escape(resolved_pdf_href)}">PDF</a>'
        )
    archive_link = (
        f'<a href="{_escape(archive_href)}">{_escape(labels["archive"])}</a>'
        if archive_href
        else ""
    )
    toc_block = _toc_html(report)
    feedback_fields = (
        (labels["relevance"], "relevance"),
        (labels["accuracy"], "accuracy"),
        (labels["analysis_value"], "analysis_value"),
        (labels["satisfaction"], "satisfaction"),
    )
    feedback_controls = "".join(
        f'<label>{_escape(label)}<select data-feedback="{key}">'
        f'<option value="">{_escape(labels["unrated"])}</option>'
        + "".join(
            f"<option value={score}>{score}/5</option>" for score in range(1, 6)
        )
        + "</select></label>"
        for label, key in feedback_fields
    )
    feedback_print = "　".join(
        f"{label}: __/5" for label, _key in feedback_fields
    )
    return f"""<!doctype html>
<html lang="{_escape(language)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https: data: file:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{_escape(report.get('title'))}</title>
<style>
:root{{--ink:#18202a;--muted:#637083;--paper:#f5f2eb;--card:#fff;--line:#dfe3e8;--blue:#234a70;--red:#a53b2e;--gold:#a67424;--soft:#eef3f7}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:var(--ink);background:var(--paper);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;line-height:1.72}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}.shell{{width:min(1120px,calc(100% - 32px));margin:0 auto}}.masthead{{padding:56px 0 38px;background:linear-gradient(135deg,#172a3d,#254f6f);color:#fff;border-bottom:5px solid #bd8a39}}.eyebrow{{letter-spacing:.16em;text-transform:uppercase;color:#e5c98e;font-size:13px}}h1{{font-family:Georgia,"Noto Serif CJK SC",serif;font-size:clamp(34px,5vw,60px);line-height:1.14;margin:10px 0 16px;max-width:900px}}.metadata{{display:flex;gap:10px 24px;flex-wrap:wrap;color:#d9e3ec;font-size:14px}}.toolbar{{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(12px)}}.toolbar-inner{{display:flex;align-items:center;gap:16px;padding:12px 0}}.toolbar nav{{display:flex;gap:18px;font-weight:700}}.toolbar input{{margin-left:auto;min-width:260px;padding:9px 12px;border:1px solid var(--line);border-radius:8px}}.tools{{display:flex;gap:10px;white-space:nowrap}}main{{padding:34px 0 70px}}.summary,.module,.analysis-module,.evaluation,.feedback,.pending{{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 24px rgba(25,36,48,.05);margin:0 0 24px;padding:28px}}.summary h2,.module-label,.analysis-module>h2,.evaluation>h2,.feedback>h2{{font-family:Georgia,"Noto Serif CJK SC",serif;color:var(--blue);font-size:28px;margin:0 0 16px}}.summary ul{{margin:0;padding-left:24px}}.module-label{{font-size:34px;border-bottom:3px solid var(--gold);padding-bottom:10px}}.content-section{{padding:24px 0 6px;border-bottom:1px solid var(--line)}}.content-section:last-child{{border:0}}.content-section>h2{{font-size:24px;margin:0 0 16px}}.source-group{{margin:18px 0 28px}}.source-heading{{display:flex;align-items:center;justify-content:space-between;background:var(--soft);border-left:5px solid var(--blue);padding:10px 14px;margin-bottom:4px}}.source-heading h3{{font-size:19px;margin:0}}.source-heading span{{font-size:13px;color:var(--muted)}}.brief{{display:grid;grid-template-columns:38px minmax(220px,300px) minmax(0,1fr);gap:14px 18px;padding:18px 6px;border-bottom:1px dashed var(--line);break-inside:avoid}}.brief-heading{{grid-column:1/-1;grid-row:1;display:grid;grid-template-columns:38px minmax(0,1fr) auto;gap:12px;align-items:start}}.ordinal{{font:700 18px Georgia;color:var(--gold);padding-top:2px}}.brief h4{{font-size:17px;line-height:1.5;margin:0}}.translated-title{{font-weight:700;margin:5px 0 0;color:#35465a}}.story-time{{margin:5px 0 0;color:var(--muted);font-size:12px}}.badges{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.badge{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;background:#eef2f5;color:#4d5a67}}.badge.status{{background:#f5e8e2;color:var(--red)}}.badge.rank{{background:#f6edd8;color:#7a581c}}.brief>.tldr{{grid-column:3;grid-row:2;margin:0;color:#344150}}.brief:not(.has-image)>.tldr{{grid-column:2/-1}}.tldr span{{font-size:11px;font-weight:800;letter-spacing:.08em;color:var(--red);margin-right:9px}}.brief>figure{{grid-column:2;grid-row:2;margin:0}}.brief figure img{{display:block;width:100%;max-width:300px;aspect-ratio:16/9;object-fit:cover;border-radius:8px}}.brief figcaption{{font-size:12px;color:var(--muted);line-height:1.45;margin-top:5px}}.empty-note,.evaluation-pending{{padding:18px;background:#f7f8f9;border:1px dashed #c9d0d7;border-radius:8px;color:var(--muted)}}.pending li{{display:flex;gap:10px;justify-content:space-between;border-bottom:1px solid var(--line);padding:8px 0}}.pending li span{{color:var(--muted);font-size:13px}}.analysis-domain>h3{{font-size:23px;margin:30px 0 14px;border-left:5px solid var(--red);padding-left:12px}}.analysis-card{{border:1px solid var(--line);border-radius:12px;margin:0 0 20px;padding:24px;break-inside:avoid}}.analysis-card>h4{{font-family:Georgia,"Noto Serif CJK SC",serif;font-size:23px;line-height:1.5;margin:0 0 10px}}.analysis-meta{{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:13px;padding-bottom:15px;border-bottom:1px solid var(--line)}}.analysis-part{{margin-top:18px}}.analysis-part h5{{font-size:15px;color:var(--red);margin:0 0 6px}}.analysis-part p,.analysis-part ul{{margin-top:0}}.stakeholder-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}.stakeholder{{background:#f7f5ef;border-radius:8px;padding:14px}}.stakeholder p{{margin:4px 0}}.stakeholder small{{color:var(--muted)}}.follow-up{{border-top:1px solid var(--line);margin-top:24px;padding-top:18px}}.evaluation-score{{display:flex;align-items:baseline;gap:6px;margin:4px 0 18px}}.evaluation-score strong{{font:700 52px Georgia;color:var(--red)}}.evaluation-score span{{color:var(--muted)}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid var(--line);padding:10px;vertical-align:top}}th{{background:var(--soft)}}.score{{font-weight:800;color:var(--red);white-space:nowrap}}.feedback-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}label{{font-size:13px;color:var(--muted)}}select,textarea{{width:100%;margin-top:5px;padding:9px;border:1px solid var(--line);border-radius:7px;background:#fff}}textarea{{min-height:100px}}.feedback .comment{{display:block;margin-top:16px}}button{{margin-top:14px;background:var(--blue);color:#fff;border:0;border-radius:8px;padding:10px 16px;font-weight:700;cursor:pointer}}.feedback-note{{color:var(--muted);font-size:12px}}.feedback-print{{display:none}}footer{{color:var(--muted);font-size:12px;padding:0 0 34px;text-align:center}}.hidden-by-search{{display:none!important}}
.analysis-narrative{{max-width:76ch;margin:22px 0 18px;font-family:Georgia,"Noto Serif CJK SC",serif;font-size:17px;line-height:1.92;color:#253342}}.analysis-narrative p{{margin:0 0 1em}}.analysis-notebook{{margin-top:20px;border-top:1px solid var(--line);padding-top:14px}}.analysis-notebook summary{{cursor:pointer;color:var(--blue);font-weight:800;list-style-position:outside}}.analysis-notebook-body{{margin-top:12px;padding:2px 16px 12px;border-left:3px solid var(--line);color:#374556}}
.summary,.module,.content-section,.analysis-module,.analysis-domain,.evaluation,.feedback{{scroll-margin-top:88px}}.toc-toggle{{position:fixed;z-index:32;left:14px;top:50%;display:flex;flex-direction:column;align-items:center;gap:6px;width:42px;margin:0;padding:13px 8px;transform:translateY(-50%);border:1px solid rgba(35,74,112,.2);border-radius:10px;background:rgba(255,255,255,.96);box-shadow:0 8px 24px rgba(25,36,48,.14);color:var(--blue);font-size:12px;letter-spacing:.12em;backdrop-filter:blur(12px);transition:opacity .2s,transform .2s}}.toc-toggle span:first-child{{font-size:17px;line-height:1}}body.toc-open .toc-toggle{{opacity:0;pointer-events:none;transform:translate(-12px,-50%)}}.report-toc{{position:fixed;z-index:31;left:14px;top:84px;bottom:18px;width:286px;display:flex;flex-direction:column;overflow:hidden;border:1px solid rgba(35,74,112,.16);border-radius:14px;background:rgba(255,255,255,.97);box-shadow:0 16px 42px rgba(22,42,61,.18);backdrop-filter:blur(16px);transform:translateX(calc(-100% - 30px));transition:transform .24s ease}}body.toc-open .report-toc{{transform:translateX(0)}}.toc-heading{{display:flex;align-items:center;justify-content:space-between;padding:16px 16px 12px;border-bottom:1px solid var(--line);color:var(--blue)}}.toc-heading strong{{font:700 18px Georgia,"Noto Serif CJK SC",serif}}.toc-close{{margin:0;padding:5px 9px;border:1px solid var(--line);border-radius:7px;background:var(--soft);color:var(--blue);font-size:12px}}.toc-nav{{overflow-y:auto;padding:10px}}.toc-link{{display:block;margin:2px 0;padding:7px 10px;border-left:3px solid transparent;border-radius:6px;color:#35465a;font-size:14px;line-height:1.4}}.toc-link:hover{{background:var(--soft);text-decoration:none}}.toc-link.toc-level-0{{margin-top:7px;font-weight:800;color:var(--blue)}}.toc-link.toc-level-1{{padding-left:22px;font-size:13px}}.toc-link.active{{border-left-color:var(--gold);background:#f6edd8;color:#634718}}.toc-scrim{{position:fixed;z-index:30;inset:0;visibility:hidden;background:rgba(18,30,42,.22);opacity:0;transition:opacity .2s,visibility .2s}}body.toc-open .toc-scrim{{visibility:visible;opacity:1}}
@media(min-width:1500px){{.toc-scrim{{display:none}}}}
@media(max-width:720px){{.toolbar-inner{{align-items:flex-start;flex-wrap:wrap}}.toolbar input{{order:3;margin:0;width:100%;min-width:0}}.tools{{margin-left:auto}}.summary,.module,.analysis-module,.evaluation,.feedback,.pending{{padding:20px}}.brief{{grid-template-columns:32px minmax(0,1fr);gap:10px 12px}}.brief-heading{{grid-column:1/-1;grid-template-columns:32px 1fr}}.badges{{grid-column:2;justify-content:flex-start}}.brief>figure{{grid-column:2;grid-row:2}}.brief.has-image>.tldr{{grid-column:2;grid-row:3}}.brief:not(.has-image)>.tldr{{grid-column:2;grid-row:2}}.feedback-grid{{grid-template-columns:1fr 1fr}}.toc-toggle{{left:8px;width:38px}}.report-toc{{left:8px;top:72px;bottom:8px;width:min(300px,calc(100vw - 24px))}}}}
@media print{{body{{background:#fff;font-size:10.5pt}}.masthead{{padding:28px 0;background:#fff!important;color:#172a3d;border-bottom:3px solid #a67424}}.eyebrow{{color:#7a581c}}.metadata{{color:#536273}}.toolbar,.toc-toggle,.report-toc,.toc-scrim,.feedback button,.feedback-note,.feedback-grid,.feedback .comment{{display:none}}.feedback-print{{display:block}}.shell{{width:auto;margin:0 14mm}}main{{padding:12px 0}}.summary,.module,.analysis-module,.evaluation,.feedback,.pending{{box-shadow:none;border:0;border-radius:0;padding:10px 0;margin:0 0 12px}}.source-group,.brief,table,figure{{break-inside:avoid}}details.analysis-notebook:not([open])>.analysis-notebook-body{{display:block!important}}.analysis-notebook summary{{list-style:none}}a{{color:#18202a}}.content-section{{break-before:auto}}}}
</style>
</head>
<body>
{toc_block}
<header class="masthead"><div class="shell"><div class="eyebrow">Daily Intelligence · {edition_labels.get(str(report.get('edition')), _escape(report.get('edition')))}</div><h1>{_escape(report.get('title'))}</h1><div class="metadata"><span>{_escape(report.get('date'))}</span><span>{_escape(labels["revision"])} r{_escape(report.get('revision'))}</span><span>{_escape(report.get('generated_at'))}</span><span>{_escape(report_id)}</span></div></div></header>
<div class="toolbar"><div class="shell toolbar-inner"><nav><a href="#module-information">{_escape(module_labels["information"])}</a><a href="#module-technology">{_escape(module_labels["technology"])}</a><a href="#analysis">{_escape(labels["analysis"])}</a><a href="#evaluation">{_escape(labels["evaluation"])}</a></nav><input id="search" type="search" placeholder="{_escape(labels["filter"])}"><div class="tools">{archive_link}{pdf_link}</div></div></div>
<main class="shell"><section class="summary" id="summary"><h2>{_escape(labels["summary"])}</h2>{_list_html(report.get('executive_summary', []), language=language)}</section>{''.join(module_blocks)}{pending_block}<section class="analysis-module" id="analysis"><h2>{_escape(labels["analysis"])}</h2>{''.join(analysis_groups)}{synthesis_block}{changes_block}{watch_block}</section><section class="evaluation" id="evaluation"><h2>{_escape(labels["evaluation"])}</h2>{_evaluation_html(evaluation, language)}</section><section class="feedback" id="feedback"><h2>{_escape(labels["feedback"])}</h2><div class="feedback-grid">{feedback_controls}</div><label class="comment">{_escape(labels["comments"])}<textarea data-feedback="comment" placeholder="{_escape(labels["feedback_placeholder"])}"></textarea></label><div class="feedback-print">{_escape(feedback_print)}<br>{_escape(labels["comments"])}: ________________________________</div><button id="download-feedback" type="button">{_escape(labels["download_feedback"])}</button><p class="feedback-note">{_escape(labels["feedback_note"])}</p></section></main><footer class="shell">{_escape(labels["footer"])}</footer>
<script>
const reportMeta={feedback_data};
const tocToggle=document.getElementById('toc-toggle');
const tocPanel=document.getElementById('report-toc');
const tocClose=document.getElementById('toc-close');
const tocScrim=document.getElementById('toc-scrim');
const tocLinks=[...document.querySelectorAll('.toc-link')];
const tocStorageKey='daily-intelligence-toc-open';
const setTocOpen=open=>{{document.body.classList.toggle('toc-open',open);tocToggle.setAttribute('aria-expanded',String(open));tocPanel.setAttribute('aria-hidden',String(!open));try{{localStorage.setItem(tocStorageKey,String(open));}}catch(_error){{}}}};
let initialTocOpen=window.matchMedia('(min-width:1740px)').matches;
try{{const savedTocState=localStorage.getItem(tocStorageKey);if(savedTocState!==null)initialTocOpen=savedTocState==='true';}}catch(_error){{}}
setTocOpen(initialTocOpen);
tocToggle.addEventListener('click',()=>setTocOpen(true));
tocClose.addEventListener('click',()=>setTocOpen(false));
tocScrim.addEventListener('click',()=>setTocOpen(false));
tocLinks.forEach(link=>link.addEventListener('click',()=>{{if(window.matchMedia('(max-width:1499px)').matches)setTocOpen(false);}}));
const tocTargets=tocLinks.map(link=>({{link,target:document.getElementById(link.getAttribute('href').slice(1))}})).filter(row=>row.target);
let tocUpdatePending=false;
const updateActiveToc=()=>{{tocUpdatePending=false;let active=tocTargets[0];for(const row of tocTargets){{if(row.target.getBoundingClientRect().top<=120)active=row;else break;}}for(const row of tocTargets){{const selected=row===active;row.link.classList.toggle('active',selected);if(selected)row.link.setAttribute('aria-current','location');else row.link.removeAttribute('aria-current');}}}};
window.addEventListener('scroll',()=>{{if(!tocUpdatePending){{tocUpdatePending=true;requestAnimationFrame(updateActiveToc);}}}},{{passive:true}});
window.addEventListener('resize',updateActiveToc);
updateActiveToc();
const search=document.getElementById('search');
search.addEventListener('input',()=>{{const q=search.value.trim().toLowerCase();document.querySelectorAll('.source-group').forEach(group=>{{const groupMatch=!q||group.dataset.search.toLowerCase().includes(q);let any=groupMatch;group.querySelectorAll('.brief').forEach(brief=>{{const match=groupMatch||brief.dataset.search.toLowerCase().includes(q);brief.classList.toggle('hidden-by-search',!match);any=any||match;}});group.classList.toggle('hidden-by-search',!any);}});}});
document.getElementById('download-feedback').addEventListener('click',()=>{{const feedback={{...reportMeta,created_at:new Date().toISOString()}};document.querySelectorAll('[data-feedback]').forEach(el=>feedback[el.dataset.feedback]=el.value);const blob=new Blob([JSON.stringify(feedback,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`feedback-${{reportMeta.report_id}}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}});
</script>
</body></html>
"""


def _reportlab_pdf(
    report: dict[str, Any],
    evaluation: dict[str, Any] | None,
    output_path: Path,
    data_dir: Path | None = None,
) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import (
            Image as PlatypusImage,
        )
        from reportlab.platypus import (
            KeepTogether,
            PageBreak,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:
        raise RuntimeError(
            "ReportLab is required for the PDF fallback; reinstall daily-intelligence-skill"
        ) from exc

    font_name = "STSong-Light"
    language = report.get("language") or "zh-CN"
    labels = _ui(language)
    module_labels = MODULE_LABELS if is_chinese_output(language) else MODULE_LABELS_EN
    edition_labels = EDITION_LABELS if is_chinese_output(language) else EDITION_LABELS_EN
    analysis_labels = (
        ANALYSIS_LABELS if is_chinese_output(language) else ANALYSIS_LABELS_EN
    )
    evaluation_labels = (
        EVALUATION_LABELS if is_chinese_output(language) else EVALUATION_LABELS_EN
    )
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "ReportBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#253242"),
        spaceAfter=5,
        wordWrap="CJK" if is_chinese_output(language) else None,
    )
    title = ParagraphStyle(
        "ReportTitle",
        parent=base,
        fontSize=24,
        leading=32,
        textColor=colors.HexColor("#173753"),
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        "ReportH1",
        parent=base,
        fontSize=19,
        leading=25,
        textColor=colors.HexColor("#234A70"),
        spaceBefore=12,
        spaceAfter=9,
    )
    h2 = ParagraphStyle(
        "ReportH2",
        parent=base,
        fontSize=14,
        leading=19,
        textColor=colors.HexColor("#8A3D32"),
        spaceBefore=9,
        spaceAfter=6,
    )
    h3 = ParagraphStyle(
        "ReportH3",
        parent=base,
        fontSize=11.5,
        leading=17,
        textColor=colors.HexColor("#234A70"),
        spaceBefore=7,
        spaceAfter=4,
    )
    small = ParagraphStyle(
        "ReportSmall",
        parent=base,
        fontSize=8,
        leading=12,
        textColor=colors.HexColor("#667383"),
    )

    def paragraph(value: object, style: ParagraphStyle = base) -> Paragraph:
        return Paragraph(_escape(value).replace("\n", "<br/>"), style)

    def bullet(value: object) -> Paragraph:
        return Paragraph(f"• {_escape(value)}", base)

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont(font_name, 8)
        canvas.setFillColor(colors.HexColor("#73808D"))
        canvas.drawCentredString(
            A4[0] / 2,
            10 * mm,
            labels["page"].format(page=document.page),
        )
        canvas.restoreState()

    story: list[Any] = [
        paragraph(report.get("title"), title),
        paragraph(
            f"{edition_labels.get(str(report.get('edition')), report.get('edition'))} · "
            f"{report.get('date')} · {labels['revision']} r{report.get('revision')}",
            small,
        ),
        Spacer(1, 5 * mm),
        paragraph(labels["summary"], h1),
    ]
    story.extend(bullet(value) for value in report.get("executive_summary", []))
    for module in ("information", "technology"):
        story.extend([Spacer(1, 4 * mm), paragraph(module_labels[module], h1)])
        for section in _ordered_sections(report, module):
            story.append(paragraph(section.get("title"), h2))
            groups = _group_items(section, language)
            if not groups:
                story.append(paragraph(section.get("coverage_note") or labels["empty"], small))
            for source, items in groups:
                story.append(
                    paragraph(
                        source.get("name") or labels["unknown_source"],
                        h3,
                    )
                )
                for rank, item in enumerate(items, start=1):
                    ref = _item_ref(item)
                    source_rank = f" [{item.get('source_rank_label')}]" if item.get("source_rank_label") else ""
                    label = f"{rank}. {item.get('title')}{source_rank}"
                    link = _safe_url(ref.get("url"))
                    linked_title = Paragraph(f'<link href="{link}">{_escape(label)}</link>', base)
                    blocks: list[Any] = [linked_title]
                    if localized_title := translated_title(item, language):
                        blocks.append(paragraph(localized_title, h3))
                    if time_info := reference_time_label(ref, language):
                        time_label, time_value = time_info
                        blocks.append(paragraph(f"{time_label}：{time_value}", small))
                    image = item.get("image")
                    if data_dir is not None and isinstance(image, dict):
                        image_path = _validated_local_image_path(image, data_dir)
                        if image_path is not None:
                            try:
                                image_width, image_height = ImageReader(
                                    str(image_path)
                                ).getSize()
                                scale = min(
                                    (80 * mm) / image_width,
                                    (45 * mm) / image_height,
                                    1.0,
                                )
                                blocks.append(
                                    PlatypusImage(
                                        str(image_path),
                                        width=image_width * scale,
                                        height=image_height * scale,
                                    )
                                )
                                caption = image.get("caption")
                                credit = image.get("credit")
                                if caption or credit:
                                    blocks.append(
                                        paragraph(
                                            " · ".join(
                                                str(value)
                                                for value in (caption, credit)
                                                if value
                                            ),
                                            small,
                                        )
                                    )
                            except (OSError, ValueError, ZeroDivisionError):
                                pass
                    blocks.append(paragraph(f"TL;DR：{item.get('tldr')}"))
                    story.append(KeepTogether(blocks))
                    story.append(Spacer(1, 2 * mm))
    story.extend([PageBreak(), paragraph(labels["analysis"], h1)])
    for domain in ("geopolitics", "ai_technology", "markets"):
        story.append(paragraph(analysis_labels[domain], h2))
        rows = [row for row in report.get("analyses", []) if row.get("domain") == domain]
        if not rows:
            story.append(paragraph(labels["analysis_empty"], small))
        for analysis in rows:
            story.append(paragraph(analysis.get("claim"), h3))
            story.extend(
                paragraph(value)
                for value in split_narrative_paragraphs(analysis.get("narrative"))
            )
            story.append(paragraph(labels["support"], h3))
            for label, key in (
                (labels["facts"], "facts"),
                (labels["history"], "historical_context"),
                (labels["dialectic"], "dialectical_analysis"),
                (labels["reasoning"], "reasoning"),
                (labels["counter"], "counter_evidence"),
                (labels["scenarios"], "scenarios"),
                (labels["implications"], "implications"),
                (labels["actions"], "actions"),
                (labels["watch"], "watch_signals"),
                (labels["invalidation"], "invalidation_signals"),
                (labels["causal"], "causal_chain"),
                (labels["assumptions"], "assumptions"),
                (labels["gaps"], "evidence_gaps"),
                (labels["horizon"], "time_horizon"),
                (labels["confidence_basis"], "confidence_rationale"),
                (labels["change_prior"], "change_from_prior"),
                (labels["decision"], "decision_relevance"),
            ):
                value = analysis.get(key)
                if not value:
                    continue
                story.append(paragraph(label, h3))
                if isinstance(value, list):
                    story.extend(bullet(row) for row in value)
                else:
                    story.append(paragraph(value))
    synthesis = report.get("cross_perspective_synthesis")
    if isinstance(synthesis, dict):
        story.append(paragraph(labels["synthesis"], h2))
        story.append(paragraph(synthesis.get("overall_judgment"), h3))
        for label, key in (
            (labels["consensus"], "consensus"),
            (labels["transmission"], "transmission_chain"),
            (labels["shared_watch"], "shared_watch_signals"),
            (labels["revision_triggers"], "revision_triggers"),
        ):
            story.append(paragraph(label, h3))
            story.extend(bullet(value) for value in synthesis.get(key, []))
        story.append(paragraph(labels["tensions"], h3))
        for tension in synthesis.get("tensions", []):
            if not isinstance(tension, dict):
                continue
            perspectives = (
                "、" if is_chinese_output(language) else ", "
            ).join(tension.get("perspectives", []))
            story.append(
                bullet(
                    f"{tension.get('issue', '')} ({perspectives}): "
                    f"{tension.get('source_of_difference', '')}"
                )
            )
    story.extend([PageBreak(), paragraph(labels["evaluation_and_feedback"], h1)])
    effective_evaluation = evaluation or report.get("quality_evaluation")
    if isinstance(effective_evaluation, dict):
        story.append(
            paragraph(
                f"{labels['evaluation_total']}: "
                f"{effective_evaluation.get('total_score')}/45",
                h2,
            )
        )
        rows = [
            [
                paragraph(labels["dimension"], small),
                paragraph(labels["score"], small),
                paragraph(labels["finding"], small),
            ]
        ]
        for row in effective_evaluation.get("dimensions", []):
            rows.append(
                [
                    paragraph(
                        evaluation_labels.get(str(row.get("id")), row.get("id")),
                        small,
                    ),
                    paragraph(f"{row.get('score')}/5", small),
                    paragraph(row.get("finding"), small),
                ]
            )
        table = Table(rows, colWidths=[34 * mm, 18 * mm, 123 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F7")),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CBD2D9")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        story.append(table)
    else:
        story.append(paragraph(labels["delivery_without_score"]))
    story.extend(
        [
            Spacer(1, 6 * mm),
            paragraph(labels["feedback"], h2),
            paragraph(
                " · ".join(
                    (
                        f"{labels['relevance']}: __/5",
                        f"{labels['accuracy']}: __/5",
                        f"{labels['analysis_value']}: __/5",
                        f"{labels['satisfaction']}: __/5",
                    )
                )
            ),
            paragraph(f"{labels['comments']}:"),
        ]
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(output_path),
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=17 * mm,
        bottomMargin=18 * mm,
        title=str(report.get("title") or "Daily Intelligence"),
        author="Daily Intelligence Skill",
    )
    document.build(story, onFirstPage=footer, onLaterPages=footer)


def _edge_pdf(
    html_path: Path,
    output_path: Path,
    *,
    html_document: str | None = None,
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page()
            page.emulate_media(media="print")

            def route_request(route: Any) -> None:
                if route.request.url.startswith(("http://", "https://")):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", route_request)
            if html_document is None:
                page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=30_000)
            else:
                page.set_content(html_document, wait_until="load", timeout=30_000)
            page.locator("img").evaluate_all(
                "(images) => images.forEach((image) => { image.loading = 'eager'; })"
            )
            page.wait_for_function(
                "() => Array.from(document.images).every((image) => image.complete)",
                timeout=30_000,
            )
            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;font-size:8px;color:#6b7280;text-align:center">'
                    '<span class="pageNumber"></span> / '
                    '<span class="totalPages"></span>'
                    "</div>"
                ),
                margin={"top": "12mm", "right": "10mm", "bottom": "16mm", "left": "10mm"},
                prefer_css_page_size=True,
            )
        finally:
            browser.close()


def render_pdf_from_html(
    html_path: Path,
    pdf_path: Path,
    report: dict[str, Any],
    evaluation: dict[str, Any] | None,
    engine: str,
    *,
    data_dir: Path | None = None,
    embedded_html: str | None = None,
) -> tuple[str, str | None]:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = pdf_path.with_suffix(".pdf.tmp")
    temporary.unlink(missing_ok=True)
    edge_error: Exception | None = None
    if engine in {"edge", "auto"}:
        try:
            _edge_pdf(html_path, temporary, html_document=embedded_html)
            temporary.replace(pdf_path)
            return "edge", None
        except Exception as exc:  # pragma: no cover - environment-dependent browser failure
            edge_error = exc
            temporary.unlink(missing_ok=True)
    try:
        _reportlab_pdf(report, evaluation, temporary, data_dir)
        temporary.replace(pdf_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    warning = None
    if edge_error is not None:
        warning = (
            "Microsoft Edge PDF rendering failed; used the ReportLab fallback: "
            f"{type(edge_error).__name__}: {edge_error}"
        )
    return "reportlab", warning


def _evaluation_map(data_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    root = data_dir / "evaluations"
    if not root.exists():
        return result
    for path in root.glob("*/*-r*.json"):
        payload = read_json(path)
        if not isinstance(payload, dict) or not payload.get("evaluated_report_id"):
            continue
        report_id = str(payload["evaluated_report_id"])
        current = result.get(report_id)
        if current is None or str(payload.get("evaluated_at", "")) >= str(
            current.get("evaluated_at", "")
        ):
            result[report_id] = payload
    return result


def render_archive_index(data_dir: Path) -> Path:
    reports_root = data_dir / "reports"
    reports_root.mkdir(parents=True, exist_ok=True)
    evaluations = _evaluation_map(data_dir)
    entries = []
    for path in reports_root.glob("*/*-r*.json"):
        report = read_json(path)
        if not isinstance(report, dict):
            continue
        report_id = str(report.get("report_id") or "")
        relative_base = path.relative_to(reports_root).with_suffix("")
        html_path = reports_root / relative_base.with_suffix(".html")
        pdf_path = reports_root / relative_base.with_suffix(".pdf")
        markdown_path = reports_root / relative_base.with_suffix(".md")
        entries.append(
            {
                "sort": (
                    str(report.get("date", "")),
                    1 if report.get("edition") == "evening" else 0,
                    int(report.get("revision", 0)),
                ),
                "date": report.get("date"),
                "language": report.get("language") or "zh-CN",
                "edition_code": report.get("edition"),
                "revision": report.get("revision"),
                "title": report.get("title"),
                "html": html_path.relative_to(reports_root).as_posix() if html_path.exists() else None,
                "pdf": pdf_path.relative_to(reports_root).as_posix() if pdf_path.exists() else None,
                "markdown": (
                    markdown_path.relative_to(reports_root).as_posix()
                    if markdown_path.exists()
                    else None
                ),
                "score": evaluations.get(report_id, report.get("quality_evaluation", {})).get(
                    "total_score"
                ),
            }
        )
    entries.sort(key=lambda row: row["sort"], reverse=True)
    interface_language = entries[0]["language"] if entries else "zh-CN"
    labels = _ui(interface_language)
    read_html = localized(interface_language, "阅读 HTML", "Read HTML")
    open_pdf = localized(interface_language, "打开 PDF", "Open PDF")
    evaluation_label = localized(interface_language, "独立评估", "Evaluation")
    evaluation_pending = localized(interface_language, "评估中", "Evaluation pending")
    cards = "".join(
        '<article><div><span class="date">'
        f'{_escape(row["date"])}</span><span class="edition">'
        f'{_escape((EDITION_LABELS if is_chinese_output(row["language"]) else EDITION_LABELS_EN).get(str(row["edition_code"]), row["edition_code"]))} · '
        f'r{_escape(row["revision"])}</span></div><h2>{_escape(row["title"])}</h2>'
        '<div class="links">'
        + "".join(
            f'<a href="{_escape(row[key])}">{label}</a>'
            for key, label in (
                ("html", read_html),
                ("pdf", open_pdf),
                ("markdown", "Markdown"),
            )
            if row.get(key)
        )
        + (
            f'<span class="score">{_escape(evaluation_label)} {row["score"]}/45</span>'
            if row.get("score") is not None
            else f'<span class="pending">{_escape(evaluation_pending)}</span>'
        )
        + "</div></article>"
        for row in entries
    )
    if not cards:
        cards = (
            f'<p class="empty">{_escape(localized(interface_language, "尚未生成本地日报。", "No local reports have been generated yet."))}</p>'
        )
    archive_title = labels["archive"]
    archive_detail = localized(
        interface_language,
        "本地 HTML 与 PDF 阅读入口；JSON/Markdown 保持事实源。",
        "Open local HTML and PDF reports; JSON and Markdown remain the source of truth.",
    )
    document = f"""<!doctype html><html lang="{_escape(interface_language)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'"><title>Daily Intelligence · {_escape(archive_title)}</title><style>:root{{--ink:#17212b;--blue:#234a70;--line:#dfe3e8;--paper:#f3f0e9}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif}}header{{background:#18324a;color:white;padding:52px 24px;border-bottom:5px solid #b8812f}}header div,main{{width:min(980px,100%);margin:auto}}h1{{font:700 clamp(34px,6vw,58px) Georgia,serif;margin:0 0 8px}}header p{{color:#dce7ef}}main{{padding:30px 20px 70px}}article{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:15px;box-shadow:0 8px 20px rgba(20,30,40,.04)}}article>div:first-child{{display:flex;gap:10px;align-items:center}}.date{{font:700 17px Georgia;color:#8a3d32}}.edition{{font-size:13px;color:#657181}}h2{{font-size:20px;margin:8px 0 16px}}.links{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}a{{color:var(--blue);font-weight:700;text-decoration:none;border:1px solid #cbd5df;border-radius:7px;padding:7px 11px}}a:hover{{background:#eef3f7}}.score,.pending{{margin-left:auto;font-size:13px;color:#657181}}.empty{{background:white;padding:30px;border-radius:10px}}</style></head><body><header><div><h1>{_escape(archive_title)}</h1><p>{_escape(archive_detail)}</p></div></header><main>{cards}</main></body></html>"""
    return write_text_atomic(reports_root / "index.html", document)


def resolve_desktop_directory(config: OutputConfig) -> Path:
    if config.desktop_dir:
        return Path(config.desktop_dir).expanduser().resolve()
    if os.name == "nt":
        user_profile = os.getenv("USERPROFILE")
        if user_profile:
            return (Path(user_profile) / "Desktop").resolve()
    return (Path.home() / "Desktop").resolve()


def write_desktop_html(
    report: dict[str, Any],
    data_dir: Path,
    config: OutputConfig,
    *,
    evaluation: dict[str, Any] | None = None,
    embedded_image_sources: dict[str, str] | None = None,
) -> Path:
    desktop_dir = resolve_desktop_directory(config)
    stem = (
        f"daily-intelligence-{report['date']}-{report['edition']}"
        f"-r{report['revision']}"
    )
    destination = desktop_dir / f"{stem}.html"
    report_dir = data_dir / "reports" / str(report["date"])
    pdf_path = report_dir / f"{report['edition']}-r{report['revision']}.pdf"
    return write_text_atomic(
        destination,
        render_report_html(
            report,
            evaluation,
            include_pdf_link="pdf" in config.formats,
            embedded_image_sources=(
                embedded_image_sources
                if embedded_image_sources is not None
                else _embedded_image_sources(report, data_dir)
            ),
            archive_href=(data_dir / "reports" / "index.html").resolve().as_uri(),
            pdf_href=pdf_path.resolve().as_uri(),
        ),
    )


def write_local_outputs(
    report: dict[str, Any],
    data_dir: Path,
    config: OutputConfig,
    *,
    evaluation: dict[str, Any] | None = None,
    open_after_finalize: bool | None = None,
) -> dict[str, Any]:
    config = validate_output_config(config)
    report_dir = data_dir / "reports" / str(report["date"])
    stem = f"{report['edition']}-r{report['revision']}"
    html_path = report_dir / f"{stem}.html"
    pdf_path = report_dir / f"{stem}.pdf"
    warnings: list[str] = []
    result: dict[str, Any] = {}
    embedded_image_sources = (
        _embedded_image_sources(report, data_dir)
        if "pdf" in config.formats
        or ("html" in config.formats and config.copy_html_to_desktop)
        else None
    )
    if "html" in config.formats:
        write_text_atomic(
            html_path,
            render_report_html(
                report,
                evaluation,
                include_pdf_link="pdf" in config.formats,
                media_path_prefix="../..",
            ),
        )
        result["html_path"] = str(html_path)
        if config.copy_html_to_desktop:
            try:
                result["desktop_html_path"] = str(
                    write_desktop_html(
                        report,
                        data_dir,
                        config,
                        evaluation=evaluation,
                        embedded_image_sources=embedded_image_sources,
                    )
                )
            except Exception as exc:
                warning = (
                    "Desktop HTML delivery failed: "
                    f"{type(exc).__name__}: {exc}. "
                    "Set output.desktop_dir to an accessible absolute directory."
                )
                warnings.append(warning)
                result["desktop_html_error"] = warning
    if "pdf" in config.formats:
        try:
            engine, warning = render_pdf_from_html(
                html_path,
                pdf_path,
                report,
                evaluation,
                config.pdf_engine,
                data_dir=data_dir,
                embedded_html=render_report_html(
                    report,
                    evaluation,
                    include_pdf_link=False,
                    embedded_image_sources=embedded_image_sources,
                ),
            )
            result.update({"pdf_path": str(pdf_path), "pdf_engine": engine})
            if warning:
                warnings.append(warning)
        except Exception as exc:  # local truth has already been persisted
            warnings.append(f"PDF output failed: {type(exc).__name__}: {exc}")
            result["pdf_error"] = warnings[-1]
    index_path = render_archive_index(data_dir)
    result["local_index_path"] = str(index_path)
    result["warnings"] = warnings
    should_open = config.open_after_finalize if open_after_finalize is None else open_after_finalize
    if should_open and html_path.exists():
        result["opened"] = bool(webbrowser.open(html_path.resolve().as_uri()))
    return result
