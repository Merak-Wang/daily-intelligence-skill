from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import OutputConfig
from .local_output import write_local_outputs
from .reporting import (
    compile_report_data,
    normalize_report_data,
    reference_time_label,
    report_content_hash,
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
STATUS_LABELS = {
    "NEW": "新增",
    "UPD": "更新",
    "CONF": "确认",
    "REV": "修正",
    "WATCH": "观察",
    "CLOSED": "关闭",
}
DOMAIN_LABELS = {
    "geopolitics": "地缘政治",
    "markets": "市场与经济",
    "ai_technology": "人工智能与技术",
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
ACCESS_LABELS = {
    "full_text": "已读正文",
    "partial": "已读部分正文",
    "metadata_only": "仅元数据",
    "verification_required": "需要人工验证",
}

GROUP_LABELS = {"information": "资讯", "technology": "技术"}
ASSESSMENT_LABELS = {
    "trend": "趋势判断",
    "risk": "风险分析",
    "learning_research": "学习与研究建议",
}
PERSPECTIVE_LABELS = {
    "geopolitics": "地缘政治专家",
    "ai_research_engineering": "AI研究与开发工程师",
    "equity_analysis": "股票分析师",
    "china_standpoint": "中国立场",
    "western_standpoint": "西方立场",
}
ANALYSIS_SECTION_LABELS = {
    "geopolitics": "从地缘政治专家的角度",
    "ai_technology": "从 AI 研究/开发工程师的角度",
    "markets": "从股票分析师的角度",
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


def ordered_sections(report: dict[str, Any], module: str) -> list[dict[str, Any]]:
    sections = [section for section in report["sections"] if section.get("module") == module]
    if report.get("schema_version") not in {"1.3", "1.4", "1.5"}:
        return sections
    by_id = {section["id"]: section for section in sections}
    return [by_id[section_id] for section_id in SECTION_GROUPS_V13[module] if section_id in by_id]


def group_items_by_source(section: dict[str, Any]) -> list[tuple[dict[str, str], list[dict]]]:
    groups: dict[str, tuple[dict[str, str], list[dict]]] = {}
    values = section.get("briefs") if "briefs" in section else section.get("items", [])
    for item in values or []:
        source = item.get("primary_source") or {
            "id": "unknown",
            "name": "未知来源",
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


def _brief_markdown(item: dict[str, Any], rank: int) -> list[str]:
    ref = item["source_ref"]
    status = STATUS_LABELS.get(item["status"], item["status"])
    source_rank = f" `{item['source_rank_label']}`" if item.get("source_rank_label") else ""
    lines = [
        f"**{rank}. [{item['title']}]({ref['url']})** `[{status}]`{source_rank}",
    ]
    if item.get("title_zh"):
        lines.extend(["", f"**中文标题：** {item['title_zh']}"])
    if time_info := reference_time_label(ref):
        label, value = time_info
        lines.extend(["", f"**{label}：** {value}"])
    lines.extend(["", f"**TL;DR：** {item['tldr']}"])
    lines.append("")
    return lines


def _event_markdown(item: dict[str, Any], title: str) -> list[str]:
    lines = [
        title,
        "",
        f"**摘要（TL;DR）：** {item['tldr']}",
        "",
        f"**为什么重要：** {item['why_it_matters']}",
        "",
        (
            f"**重要性：** {item['importance']}/100 | "
            f"**置信度：** {item['confidence']:.2f}"
        ),
        "",
        f"**重要性依据：** {item['importance_reason']}",
        "",
        "**证据与原文：**",
        "",
    ]
    for ref in item["source_refs"]:
        access = ACCESS_LABELS.get(ref["access"], ref["access"])
        time_text = ""
        if time_info := reference_time_label(ref):
            label, value = time_info
            time_text = f"，{label}：{value}"
        lines.append(
            f"- [{ref['title']}]({ref['url']}) — {access}，{ref['role']}{time_text}"
        )
    if item["evidence_notes"]:
        lines.extend(["", "**证据说明：**", ""])
        lines.extend(f"- {note}" for note in item["evidence_notes"])
    image = item.get("image")
    if image:
        lines.extend(
            [
                "",
                f"![{image['caption']}]({image['url']})",
                "",
                f"*图片来源：{image['credit']}*",
            ]
        )
    lines.append("")
    return lines


def render_report_markdown(report: dict[str, Any]) -> str:
    edition = EDITION_LABELS.get(report["edition"], report["edition"])
    lines = [
        f"# {report['title']}",
        "",
        f"- 版本：{edition}",
        f"- 修订号：{report['revision']}",
        f"- 生成时间：{report['generated_at']}",
        "",
        "**摘要**",
        "",
    ]
    lines.extend(f"- {item}" for item in report["executive_summary"])

    for module in ("information", "technology"):
        lines.extend(["", f"## {GROUP_LABELS[module]}", ""])
        for section in ordered_sections(report, module):
            lines.extend([f"### {section['title']}", ""])
            if not section.get("items") and not section.get("briefs"):
                lines.extend([section["coverage_note"], ""])
            if report.get("schema_version") == "1.5":
                for source, items in group_items_by_source(section):
                    lines.extend([f"#### [{source['name']}]({source['url']})", ""])
                    for rank, item in enumerate(items, start=1):
                        lines.extend(_brief_markdown(item, rank))
            elif report.get("schema_version") == "1.4":
                for source, items in group_items_by_source(section):
                    lines.extend([f"#### [{source['name']}]({source['url']})", ""])
                    for rank, item in enumerate(items, start=1):
                        link = item["source_refs"][0]["url"]
                        status = STATUS_LABELS.get(item["status"], item["status"])
                        lines.extend(
                            _event_markdown(
                                item,
                                f"**{rank}. [{item['title']}]({link})** `[{status}]`",
                            )
                        )
            else:
                for item in sorted(
                    section["items"],
                    key=lambda value: value["importance"],
                    reverse=True,
                ):
                    status = STATUS_LABELS.get(item["status"], item["status"])
                    lines.extend(_event_markdown(item, f"#### [{status}] {item['title']}"))
        if module == "information" and report["pending_verifications"]:
            lines.extend(["**采集与验证说明：**", ""])
            for item in report["pending_verifications"]:
                lines.append(f"- {item['source_name']}: {item.get('note', item['status'])}")

    lines.extend(["", "## 研判", ""])
    if report["analyses"]:
        last_domain = None
        for analysis in report["analyses"]:
            domain = analysis.get("domain")
            if domain != last_domain:
                section_title = ANALYSIS_SECTION_LABELS.get(
                    domain, DOMAIN_LABELS.get(domain, domain)
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
                        f"**领域：** {DOMAIN_LABELS.get(analysis['domain'], analysis['domain'])} | "
                        f"**置信度：** {analysis['confidence']:.2f} | "
                        f"**观点变化：** "
                        f"{STATE_LABELS.get(analysis['state_change'], analysis['state_change'])}"
                    ),
                    "",
                    "**证据事件：** " + ", ".join(analysis["evidence_event_ids"]),
                    "",
                    (
                        "**研判类型：** "
                        + "、".join(
                            ASSESSMENT_LABELS.get(item, item)
                            for item in analysis.get("assessment_types", [])
                        )
                    ),
                    "",
                    (
                        "**观察视角：** "
                        + "、".join(
                            PERSPECTIVE_LABELS.get(item, item)
                            for item in analysis.get("perspectives", [])
                        )
                    ),
                    "",
                    "**事实基础：**",
                    "",
                ]
            )
            lines.extend(f"- {item}" for item in analysis.get("facts", []))
            if analysis.get("narrative"):
                lines.extend(["", "**综合论述：**", "", analysis["narrative"], ""])
            if analysis.get("historical_context"):
                lines.extend([f"**历史脉络：** {analysis['historical_context']}", ""])
            if analysis.get("dialectical_analysis"):
                lines.extend([f"**辩证分析：** {analysis['dialectical_analysis']}", ""])
            if analysis.get("stakeholder_positions"):
                lines.extend(["**不同立场与利益：**", ""])
                lines.extend(
                    f"- **{position['stakeholder']}：** {position['position']} "
                    f"利益基础：{position['interests']}"
                    for position in analysis["stakeholder_positions"]
                )
            lines.extend(["", f"**推理链：** {analysis.get('reasoning', '')}", ""])
            lines.extend(["**反证与不确定性：**", ""])
            lines.extend(f"- {item}" for item in analysis["counter_evidence"])
            lines.extend(["", "**可能情景：**", ""])
            lines.extend(f"- {item}" for item in analysis.get("scenarios", []))
            lines.extend(["", "**影响与启示：**", ""])
            lines.extend(f"- {item}" for item in analysis["implications"])
            lines.extend(["", "**建议行动：**", ""])
            lines.extend(f"- {item}" for item in analysis.get("actions", []))
            lines.extend(["", "**后续观察信号：**", ""])
            lines.extend(f"- {item}" for item in analysis["watch_signals"])
            lines.extend(["", "**观点失效信号：**", ""])
            lines.extend(f"- {item}" for item in analysis.get("invalidation_signals", []))
    else:
        lines.append("本版没有形成达到证据门槛的研判。")

    if report["changes"]:
        lines.extend(["", "### 日间新增、确认与修正", ""])
        lines.extend(f"- {item}" for item in report["changes"])

    if report.get("tomorrow_watch_items"):
        lines.extend(["", "### 次日观察项", ""])
        lines.extend(f"- {item}" for item in report["tomorrow_watch_items"])

    evaluation = report.get("quality_evaluation")
    if evaluation:
        lines.extend(
            [
                "",
                "## 质量评估与用户反馈",
                "",
                f"**独立评估总分：{evaluation['total_score']}/45**",
                "",
                "| 维度 | 得分 | 重点结论 |",
                "| --- | ---: | --- |",
            ]
        )
        lines.extend(
            f"| {EVALUATION_LABELS.get(item['id'], item['id'])} | {item['score']}/5 | "
            f"{item['finding']} |"
            for item in evaluation["dimensions"]
        )
        for title, key in (
            ("主要缺陷", "main_defects"),
            ("证据不足项", "insufficient_evidence"),
            ("改进建议", "improvements"),
        ):
            lines.extend(["", f"### {title}", ""])
            values = evaluation.get(key, [])
            lines.extend(f"- {value}" for value in values or ["无"])
        lines.extend(
            [
                "",
                "### 用户反馈",
                "",
                "- 相关性：__/5",
                "- 准确性：__/5",
                "- 分析价值：__/5",
                "- 整体满意度：__/5",
                "- 补充意见：",
            ]
        )
    elif report.get("schema_version") == "1.5":
        lines.extend(
            [
                "",
                "## 质量评估与用户反馈",
                "",
                "独立评估将在日报发布后异步补充；评估意见不阻塞本版发布。",
                "",
                "### 用户反馈",
                "",
                "- 相关性：__/5",
                "- 准确性：__/5",
                "- 分析价值：__/5",
                "- 整体满意度：__/5",
                "- 补充意见：",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def save_report(
    input_path: Path,
    index_path: Path,
    data_dir: Path,
    output_config: OutputConfig | None = None,
) -> dict[str, Any]:
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

    errors, validation_warnings = validate_report_data(report, index, existing_events)
    warnings = [*compile_warnings, *validation_warnings]
    if errors:
        raise ValueError("Report validation failed: " + "; ".join(errors))

    json_path = report_dir / f"{edition}-r{revision}.json"
    markdown_path = report_dir / f"{edition}-r{revision}.md"
    write_immutable_json(json_path, report)
    write_text_atomic(markdown_path, render_report_markdown(report))
    write_json(data_dir / "reports" / f"latest-{edition}.json", report)
    local_outputs = write_local_outputs(report, data_dir, output_config or OutputConfig())
    warnings.extend(local_outputs.pop("warnings", []))
    semantic_cache_path = update_semantic_cache_from_report(report, index, data_dir)
    state_paths = (
        update_continuity_state(report, data_dir)
        if report.get("quality_evaluation") or report.get("schema_version") != "1.5"
        else {}
    )

    return {
        "report_id": report["report_id"],
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        **local_outputs,
        "warnings": warnings,
        "state_paths": state_paths,
        "content_hash": report_content_hash(report),
        "evaluation_status": report.get("evaluation_status", "completed"),
        "semantic_cache_path": str(semantic_cache_path),
    }


def save_evaluation(
    input_path: Path,
    report_path: Path,
    data_dir: Path,
    output_config: OutputConfig | None = None,
) -> dict[str, Any]:
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
