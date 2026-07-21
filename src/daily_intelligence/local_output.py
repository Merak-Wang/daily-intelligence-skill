# ruff: noqa: E501
from __future__ import annotations

import html
import json
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import OutputConfig, validate_output_config
from .reporting import reference_time_label
from .storage import write_text_atomic
from .taxonomy import SECTION_GROUPS_V13
from .utils import read_json

EDITION_LABELS = {"morning": "晨间版", "evening": "晚间版"}
MODULE_LABELS = {"information": "资讯", "technology": "技术"}
STATUS_LABELS = {
    "NEW": "新增",
    "UPD": "更新",
    "CONF": "确认",
    "REV": "修正",
    "WATCH": "观察",
    "CLOSED": "关闭",
}
ANALYSIS_LABELS = {
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


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _safe_url(value: object) -> str:
    url = str(value or "")
    parsed = urlsplit(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return _escape(url)
    return "#"


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


def _group_items(section: dict[str, Any]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    values = section.get("briefs") if "briefs" in section else section.get("items", [])
    groups: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for item in values or []:
        source = item.get("primary_source")
        if not isinstance(source, dict):
            refs = item.get("source_refs") or [item.get("source_ref") or {}]
            source = {
                "id": "unknown",
                "name": "未知来源",
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


def _list_html(values: list[object], *, css_class: str = "") -> str:
    if not values:
        return '<p class="muted">无</p>'
    class_attr = f' class="{_escape(css_class)}"' if css_class else ""
    return f"<ul{class_attr}>" + "".join(f"<li>{_escape(value)}</li>" for value in values) + "</ul>"


def _item_ref(item: dict[str, Any]) -> dict[str, Any]:
    ref = item.get("source_ref")
    if isinstance(ref, dict):
        return ref
    refs = item.get("source_refs") or []
    return refs[0] if refs and isinstance(refs[0], dict) else {}


def _brief_html(item: dict[str, Any], rank: int) -> str:
    ref = _item_ref(item)
    status = STATUS_LABELS.get(str(item.get("status")), str(item.get("status") or ""))
    source_rank = item.get("source_rank_label")
    event_id = item.get("featured_event_id") or item.get("event_id")
    anchor = f' id="event-{_escape(event_id)}"' if event_id else ""
    badges = [f'<span class="badge status">{_escape(status)}</span>'] if status else []
    if source_rank:
        badges.append(f'<span class="badge rank">{_escape(source_rank)}</span>')
    title = _external_link(item.get("title", "无标题"), ref.get("url"), css_class="story-link")
    title_zh = item.get("title_zh")
    translation = (
        f'<p class="translated-title">{_escape(title_zh)}</p>' if title_zh else ""
    )
    time_html = ""
    if time_info := reference_time_label(ref):
        label, value = time_info
        time_html = f'<p class="story-time">{_escape(label)}：{_escape(value)}</p>'
    image = item.get("image")
    figure = ""
    if isinstance(image, dict) and image.get("url"):
        figure = (
            '<figure><img loading="lazy" referrerpolicy="no-referrer" '
            f'src="{_safe_url(image.get("url"))}" alt="{_escape(image.get("caption"))}">'
            f'<figcaption>{_escape(image.get("caption"))} · '
            f'{_escape(image.get("credit"))}</figcaption></figure>'
        )
    return (
        f'<article class="brief"{anchor} data-search="{_escape(item.get("title"))} '
        f'{_escape(title_zh)} {_escape(item.get("tldr"))}">'
        '<div class="brief-heading">'
        f'<span class="ordinal">{rank:02d}</span><div><h4>{title}</h4>{translation}{time_html}</div>'
        f'<div class="badges">{"".join(badges)}</div></div>'
        f'<p class="tldr"><span>TL;DR</span>{_escape(item.get("tldr"))}</p>{figure}</article>'
    )


def _source_section_html(source: dict[str, Any], items: list[dict[str, Any]]) -> str:
    source_name = source.get("name") or "未知来源"
    stories = "".join(_brief_html(item, rank) for rank, item in enumerate(items, start=1))
    return (
        f'<section class="source-group" data-search="{_escape(source_name)}">'
        '<div class="source-heading">'
        f'<h3>{_external_link(source_name, source.get("url"))}</h3>'
        f'<span>{len(items)} 条</span></div>{stories}</section>'
    )


def _analysis_html(analysis: dict[str, Any]) -> str:
    event_links = []
    for event_id in analysis.get("evidence_event_ids", []):
        event_links.append(f'<a href="#event-{_escape(event_id)}">{_escape(event_id)}</a>')
    stakeholder_rows = "".join(
        '<div class="stakeholder"><strong>'
        f'{_escape(row.get("stakeholder"))}</strong><p>{_escape(row.get("position"))}</p>'
        f'<small>利益基础：{_escape(row.get("interests"))}</small></div>'
        for row in analysis.get("stakeholder_positions", [])
        if isinstance(row, dict)
    )
    sections = [
        ("事实基础", _list_html(analysis.get("facts", []))),
        ("综合论述", f'<p>{_escape(analysis.get("narrative"))}</p>'),
        ("历史脉络", f'<p>{_escape(analysis.get("historical_context"))}</p>'),
        ("辩证分析", f'<p>{_escape(analysis.get("dialectical_analysis"))}</p>'),
        ("推理链", f'<p>{_escape(analysis.get("reasoning"))}</p>'),
        ("反证与不确定性", _list_html(analysis.get("counter_evidence", []))),
        ("可能情景", _list_html(analysis.get("scenarios", []))),
        ("影响与启示", _list_html(analysis.get("implications", []))),
        ("建议行动", _list_html(analysis.get("actions", []))),
        ("后续观察信号", _list_html(analysis.get("watch_signals", []))),
        ("观点失效信号", _list_html(analysis.get("invalidation_signals", []))),
    ]
    body = "".join(
        f'<section class="analysis-part"><h5>{title}</h5>{content}</section>'
        for title, content in sections
        if content not in {'<p></p>', '<p class="muted">无</p>'}
    )
    if stakeholder_rows:
        body += (
            '<section class="analysis-part"><h5>不同立场与利益</h5>'
            f'<div class="stakeholder-grid">{stakeholder_rows}</div></section>'
        )
    confidence = analysis.get("confidence")
    confidence_text = f"{float(confidence):.0%}" if isinstance(confidence, (int, float)) else "-"
    return (
        '<article class="analysis-card">'
        f'<h4>{_escape(analysis.get("claim"))}</h4>'
        '<div class="analysis-meta">'
        f'<span>置信度 {confidence_text}</span>'
        f'<span>证据 {" · ".join(event_links) or "未绑定"}</span></div>{body}</article>'
    )


def _evaluation_html(evaluation: dict[str, Any] | None) -> str:
    if not evaluation:
        return (
            '<div class="evaluation-pending"><strong>独立评估处理中</strong>'
            '<p>日报已经交付，评估 Agent 将异步补充九维评分与修改意见。</p></div>'
        )
    dimensions = "".join(
        '<tr><td>'
        f'{_escape(EVALUATION_LABELS.get(str(row.get("id")), row.get("id")))}</td>'
        f'<td><span class="score">{_escape(row.get("score"))}/5</span></td>'
        f'<td>{_escape(row.get("finding"))}</td></tr>'
        for row in evaluation.get("dimensions", [])
        if isinstance(row, dict)
    )
    notes = "".join(
        f'<section><h4>{title}</h4>{_list_html(evaluation.get(key, []))}</section>'
        for title, key in (
            ("主要缺陷", "main_defects"),
            ("证据不足项", "insufficient_evidence"),
            ("改进建议", "improvements"),
        )
    )
    return (
        '<div class="evaluation-score">'
        f'<strong>{_escape(evaluation.get("total_score"))}</strong><span>/ 45</span></div>'
        '<div class="table-wrap"><table><thead><tr><th>维度</th><th>得分</th>'
        f'<th>重点结论</th></tr></thead><tbody>{dimensions}</tbody></table></div>{notes}'
    )


def render_report_html(
    report: dict[str, Any],
    evaluation: dict[str, Any] | None = None,
    *,
    include_pdf_link: bool = True,
) -> str:
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
                _source_section_html(source, items)
                for source, items in _group_items(section)
            )
            empty_note = ""
            if not source_groups:
                empty_note = (
                    '<div class="empty-note">'
                    f'{_escape(section.get("coverage_note") or "本时段暂无可发布内容。")}</div>'
                )
            section_blocks.append(
                f'<section class="content-section" id="{_escape(section.get("id"))}">'
                f'<h2>{_escape(section.get("title"))}</h2>{source_groups}{empty_note}</section>'
            )
        module_blocks.append(
            f'<section class="module" id="module-{module}"><div class="module-label">'
            f'{MODULE_LABELS[module]}</div>{"".join(section_blocks)}</section>'
        )

    pending = "".join(
        '<li>'
        f'{_external_link(item.get("source_name"), item.get("url"))}'
        f'<span>{_escape(item.get("note") or item.get("status"))}</span></li>'
        for item in report.get("pending_verifications", [])
        if isinstance(item, dict)
    )
    pending_block = (
        '<aside class="pending"><h3>待验证来源</h3><ul>' + pending + "</ul></aside>"
        if pending
        else ""
    )

    analysis_groups = []
    for domain in ("geopolitics", "ai_technology", "markets"):
        analyses = [row for row in report.get("analyses", []) if row.get("domain") == domain]
        cards = "".join(_analysis_html(row) for row in analyses)
        if not cards:
            cards = '<div class="empty-note">本版没有形成达到证据门槛的该领域研判。</div>'
        analysis_groups.append(
            f'<section class="analysis-domain"><h3>{ANALYSIS_LABELS[domain]}</h3>{cards}</section>'
        )

    changes = report.get("changes", [])
    changes_block = (
        '<section class="follow-up"><h3>日间新增、确认与修正</h3>'
        f'{_list_html(changes)}</section>'
        if changes
        else ""
    )
    watch = report.get("tomorrow_watch_items", [])
    watch_block = (
        '<section class="follow-up"><h3>次日观察项</h3>'
        f'{_list_html(watch)}</section>'
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
        pdf_link = (
            f'<a href="{_escape(str(report.get("edition")))}-r'
            f'{_escape(report.get("revision"))}.pdf">PDF</a>'
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src https: data: file:; connect-src 'none'; base-uri 'none'; form-action 'none'">
<title>{_escape(report.get('title'))}</title>
<style>
:root{{--ink:#18202a;--muted:#637083;--paper:#f5f2eb;--card:#fff;--line:#dfe3e8;--blue:#234a70;--red:#a53b2e;--gold:#a67424;--soft:#eef3f7}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;color:var(--ink);background:var(--paper);font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;line-height:1.72}}
a{{color:var(--blue);text-decoration:none}}a:hover{{text-decoration:underline}}.shell{{width:min(1120px,calc(100% - 32px));margin:0 auto}}.masthead{{padding:56px 0 38px;background:linear-gradient(135deg,#172a3d,#254f6f);color:#fff;border-bottom:5px solid #bd8a39}}.eyebrow{{letter-spacing:.16em;text-transform:uppercase;color:#e5c98e;font-size:13px}}h1{{font-family:Georgia,"Noto Serif CJK SC",serif;font-size:clamp(34px,5vw,60px);line-height:1.14;margin:10px 0 16px;max-width:900px}}.metadata{{display:flex;gap:10px 24px;flex-wrap:wrap;color:#d9e3ec;font-size:14px}}.toolbar{{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(12px)}}.toolbar-inner{{display:flex;align-items:center;gap:16px;padding:12px 0}}.toolbar nav{{display:flex;gap:18px;font-weight:700}}.toolbar input{{margin-left:auto;min-width:260px;padding:9px 12px;border:1px solid var(--line);border-radius:8px}}.tools{{display:flex;gap:10px;white-space:nowrap}}main{{padding:34px 0 70px}}.summary,.module,.analysis-module,.evaluation,.feedback,.pending{{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:0 8px 24px rgba(25,36,48,.05);margin:0 0 24px;padding:28px}}.summary h2,.module-label,.analysis-module>h2,.evaluation>h2,.feedback>h2{{font-family:Georgia,"Noto Serif CJK SC",serif;color:var(--blue);font-size:28px;margin:0 0 16px}}.summary ul{{margin:0;padding-left:24px}}.module-label{{font-size:34px;border-bottom:3px solid var(--gold);padding-bottom:10px}}.content-section{{padding:24px 0 6px;border-bottom:1px solid var(--line)}}.content-section:last-child{{border:0}}.content-section>h2{{font-size:24px;margin:0 0 16px}}.source-group{{margin:18px 0 28px}}.source-heading{{display:flex;align-items:center;justify-content:space-between;background:var(--soft);border-left:5px solid var(--blue);padding:10px 14px;margin-bottom:4px}}.source-heading h3{{font-size:19px;margin:0}}.source-heading span{{font-size:13px;color:var(--muted)}}.brief{{padding:18px 6px;border-bottom:1px dashed var(--line);break-inside:avoid}}.brief-heading{{display:grid;grid-template-columns:38px minmax(0,1fr) auto;gap:12px;align-items:start}}.ordinal{{font:700 18px Georgia;color:var(--gold);padding-top:2px}}.brief h4{{font-size:17px;line-height:1.5;margin:0}}.translated-title{{font-weight:700;margin:5px 0 0;color:#35465a}}.story-time{{margin:5px 0 0;color:var(--muted);font-size:12px}}.badges{{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}}.badge{{display:inline-block;padding:2px 7px;border-radius:999px;font-size:11px;background:#eef2f5;color:#4d5a67}}.badge.status{{background:#f5e8e2;color:var(--red)}}.badge.rank{{background:#f6edd8;color:#7a581c}}.tldr{{margin:10px 0 0 50px;color:#344150}}.tldr span{{font-size:11px;font-weight:800;letter-spacing:.08em;color:var(--red);margin-right:9px}}figure{{margin:16px 0 0 50px}}figure img{{max-width:100%;max-height:420px;border-radius:8px}}figcaption{{font-size:12px;color:var(--muted)}}.empty-note,.evaluation-pending{{padding:18px;background:#f7f8f9;border:1px dashed #c9d0d7;border-radius:8px;color:var(--muted)}}.pending li{{display:flex;gap:10px;justify-content:space-between;border-bottom:1px solid var(--line);padding:8px 0}}.pending li span{{color:var(--muted);font-size:13px}}.analysis-domain>h3{{font-size:23px;margin:30px 0 14px;border-left:5px solid var(--red);padding-left:12px}}.analysis-card{{border:1px solid var(--line);border-radius:12px;margin:0 0 20px;padding:24px;break-inside:avoid}}.analysis-card>h4{{font-family:Georgia,"Noto Serif CJK SC",serif;font-size:23px;line-height:1.5;margin:0 0 10px}}.analysis-meta{{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:13px;padding-bottom:15px;border-bottom:1px solid var(--line)}}.analysis-part{{margin-top:18px}}.analysis-part h5{{font-size:15px;color:var(--red);margin:0 0 6px}}.analysis-part p,.analysis-part ul{{margin-top:0}}.stakeholder-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}}.stakeholder{{background:#f7f5ef;border-radius:8px;padding:14px}}.stakeholder p{{margin:4px 0}}.stakeholder small{{color:var(--muted)}}.follow-up{{border-top:1px solid var(--line);margin-top:24px;padding-top:18px}}.evaluation-score{{display:flex;align-items:baseline;gap:6px;margin:4px 0 18px}}.evaluation-score strong{{font:700 52px Georgia;color:var(--red)}}.evaluation-score span{{color:var(--muted)}}.table-wrap{{overflow:auto}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;border-bottom:1px solid var(--line);padding:10px;vertical-align:top}}th{{background:var(--soft)}}.score{{font-weight:800;color:var(--red);white-space:nowrap}}.feedback-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}label{{font-size:13px;color:var(--muted)}}select,textarea{{width:100%;margin-top:5px;padding:9px;border:1px solid var(--line);border-radius:7px;background:#fff}}textarea{{min-height:100px}}.feedback .comment{{display:block;margin-top:16px}}button{{margin-top:14px;background:var(--blue);color:#fff;border:0;border-radius:8px;padding:10px 16px;font-weight:700;cursor:pointer}}.feedback-note{{color:var(--muted);font-size:12px}}.feedback-print{{display:none}}footer{{color:var(--muted);font-size:12px;padding:0 0 34px;text-align:center}}.hidden-by-search{{display:none!important}}
@media(max-width:720px){{.toolbar-inner{{align-items:flex-start;flex-wrap:wrap}}.toolbar input{{order:3;margin:0;width:100%;min-width:0}}.tools{{margin-left:auto}}.summary,.module,.analysis-module,.evaluation,.feedback,.pending{{padding:20px}}.brief-heading{{grid-template-columns:32px 1fr}}.badges{{grid-column:2;justify-content:flex-start}}.tldr,figure{{margin-left:44px}}.feedback-grid{{grid-template-columns:1fr 1fr}}}}
@media print{{body{{background:#fff;font-size:10.5pt}}.masthead{{padding:28px 0;background:#fff!important;color:#172a3d;border-bottom:3px solid #a67424}}.eyebrow{{color:#7a581c}}.metadata{{color:#536273}}.toolbar,.feedback button,.feedback-note,.feedback-grid,.feedback .comment{{display:none}}.feedback-print{{display:block}}.shell{{width:auto;margin:0 14mm}}main{{padding:12px 0}}.summary,.module,.analysis-module,.evaluation,.feedback,.pending{{box-shadow:none;border:0;border-radius:0;padding:10px 0;margin:0 0 12px}}.source-group,.brief,.analysis-card,table,figure{{break-inside:avoid}}a{{color:#18202a}}.content-section{{break-before:auto}}}}
</style>
</head>
<body>
<header class="masthead"><div class="shell"><div class="eyebrow">Daily Intelligence · {EDITION_LABELS.get(str(report.get('edition')), _escape(report.get('edition')))}</div><h1>{_escape(report.get('title'))}</h1><div class="metadata"><span>{_escape(report.get('date'))}</span><span>修订 r{_escape(report.get('revision'))}</span><span>{_escape(report.get('generated_at'))}</span><span>{_escape(report_id)}</span></div></div></header>
<div class="toolbar"><div class="shell toolbar-inner"><nav><a href="#module-information">资讯</a><a href="#module-technology">技术</a><a href="#analysis">研判</a><a href="#evaluation">评估</a></nav><input id="search" type="search" placeholder="筛选标题、摘要或来源"><div class="tools"><a href="../index.html">日报中心</a>{pdf_link}</div></div></div>
<main class="shell"><section class="summary"><h2>今日摘要</h2>{_list_html(report.get('executive_summary', []))}</section>{''.join(module_blocks)}{pending_block}<section class="analysis-module" id="analysis"><h2>研判</h2>{''.join(analysis_groups)}{changes_block}{watch_block}</section><section class="evaluation" id="evaluation"><h2>质量评估</h2>{_evaluation_html(evaluation)}</section><section class="feedback"><h2>用户反馈</h2><div class="feedback-grid">{''.join(f'<label>{label}<select data-feedback="{key}"><option value="">未评分</option>{"".join(f"<option value={score}>{score}/5</option>" for score in range(1,6))}</select></label>' for label,key in (("相关性","relevance"),("准确性","accuracy"),("分析价值","analysis_value"),("整体满意度","satisfaction")))}</div><label class="comment">补充意见<textarea data-feedback="comment" placeholder="这些反馈可作为后续日报个性化输入。"></textarea></label><div class="feedback-print">相关性：__/5　准确性：__/5　分析价值：__/5　整体满意度：__/5<br>补充意见：________________________________</div><button id="download-feedback" type="button">下载反馈 JSON</button><p class="feedback-note">本地文件不会自动上传数据。请把下载的 JSON 交给 Hermes，作为下一版的人工反馈输入。</p></section></main><footer class="shell">本地 JSON/Markdown 为事实源；HTML/PDF 是可重新生成的阅读投影。</footer>
<script>
const reportMeta={feedback_data};
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
) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
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
    pdfmetrics.registerFont(UnicodeCIDFont(font_name))
    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "ChineseBody",
        parent=styles["BodyText"],
        fontName=font_name,
        fontSize=9.5,
        leading=15,
        textColor=colors.HexColor("#253242"),
        spaceAfter=5,
        wordWrap="CJK",
    )
    title = ParagraphStyle(
        "ChineseTitle",
        parent=base,
        fontSize=24,
        leading=32,
        textColor=colors.HexColor("#173753"),
        alignment=TA_CENTER,
        spaceAfter=12,
    )
    h1 = ParagraphStyle(
        "ChineseH1",
        parent=base,
        fontSize=19,
        leading=25,
        textColor=colors.HexColor("#234A70"),
        spaceBefore=12,
        spaceAfter=9,
    )
    h2 = ParagraphStyle(
        "ChineseH2",
        parent=base,
        fontSize=14,
        leading=19,
        textColor=colors.HexColor("#8A3D32"),
        spaceBefore=9,
        spaceAfter=6,
    )
    h3 = ParagraphStyle(
        "ChineseH3",
        parent=base,
        fontSize=11.5,
        leading=17,
        textColor=colors.HexColor("#234A70"),
        spaceBefore=7,
        spaceAfter=4,
    )
    small = ParagraphStyle(
        "ChineseSmall",
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
        canvas.drawCentredString(A4[0] / 2, 10 * mm, f"第 {document.page} 页")
        canvas.restoreState()

    story: list[Any] = [
        paragraph(report.get("title"), title),
        paragraph(
            f"{EDITION_LABELS.get(str(report.get('edition')), report.get('edition'))} · "
            f"{report.get('date')} · 修订 r{report.get('revision')}",
            small,
        ),
        Spacer(1, 5 * mm),
        paragraph("今日摘要", h1),
    ]
    story.extend(bullet(value) for value in report.get("executive_summary", []))
    for module in ("information", "technology"):
        story.extend([Spacer(1, 4 * mm), paragraph(MODULE_LABELS[module], h1)])
        for section in _ordered_sections(report, module):
            story.append(paragraph(section.get("title"), h2))
            groups = _group_items(section)
            if not groups:
                story.append(paragraph(section.get("coverage_note") or "本时段暂无内容。", small))
            for source, items in groups:
                story.append(paragraph(source.get("name") or "未知来源", h3))
                for rank, item in enumerate(items, start=1):
                    ref = _item_ref(item)
                    source_rank = f" [{item.get('source_rank_label')}]" if item.get("source_rank_label") else ""
                    label = f"{rank}. {item.get('title')}{source_rank}"
                    link = _safe_url(ref.get("url"))
                    linked_title = Paragraph(f'<link href="{link}">{_escape(label)}</link>', base)
                    blocks: list[Any] = [linked_title]
                    if item.get("title_zh"):
                        blocks.append(paragraph(item.get("title_zh"), h3))
                    if time_info := reference_time_label(ref):
                        time_label, time_value = time_info
                        blocks.append(paragraph(f"{time_label}：{time_value}", small))
                    blocks.append(paragraph(f"TL;DR：{item.get('tldr')}"))
                    story.append(KeepTogether(blocks))
                    story.append(Spacer(1, 2 * mm))
    story.extend([PageBreak(), paragraph("研判", h1)])
    for domain in ("geopolitics", "ai_technology", "markets"):
        story.append(paragraph(ANALYSIS_LABELS[domain], h2))
        rows = [row for row in report.get("analyses", []) if row.get("domain") == domain]
        if not rows:
            story.append(paragraph("本版没有形成达到证据门槛的该领域研判。", small))
        for analysis in rows:
            story.append(paragraph(analysis.get("claim"), h3))
            for label, key in (
                ("事实基础", "facts"),
                ("综合论述", "narrative"),
                ("历史脉络", "historical_context"),
                ("辩证分析", "dialectical_analysis"),
                ("推理链", "reasoning"),
                ("反证与不确定性", "counter_evidence"),
                ("可能情景", "scenarios"),
                ("影响与启示", "implications"),
                ("建议行动", "actions"),
                ("后续观察信号", "watch_signals"),
                ("观点失效信号", "invalidation_signals"),
            ):
                value = analysis.get(key)
                if not value:
                    continue
                story.append(paragraph(label, h3))
                if isinstance(value, list):
                    story.extend(bullet(row) for row in value)
                else:
                    story.append(paragraph(value))
    story.extend([PageBreak(), paragraph("质量评估与用户反馈", h1)])
    effective_evaluation = evaluation or report.get("quality_evaluation")
    if isinstance(effective_evaluation, dict):
        story.append(paragraph(f"独立评估总分：{effective_evaluation.get('total_score')}/45", h2))
        rows = [[paragraph("维度", small), paragraph("得分", small), paragraph("结论", small)]]
        for row in effective_evaluation.get("dimensions", []):
            rows.append(
                [
                    paragraph(EVALUATION_LABELS.get(str(row.get("id")), row.get("id")), small),
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
        story.append(paragraph("独立评估处理中，日报交付不等待评分。"))
    story.extend(
        [
            Spacer(1, 6 * mm),
            paragraph("用户反馈", h2),
            paragraph("相关性：__/5　准确性：__/5　分析价值：__/5　整体满意度：__/5"),
            paragraph("补充意见："),
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


def _edge_pdf(html_path: Path, output_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        try:
            page = browser.new_page()

            def route_request(route: Any) -> None:
                if route.request.url.startswith(("http://", "https://")):
                    route.abort()
                else:
                    route.continue_()

            page.route("**/*", route_request)
            page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=30_000)
            page.emulate_media(media="print")
            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="width:100%;font-size:8px;color:#6b7280;text-align:center">'
                    '第 <span class="pageNumber"></span> / <span class="totalPages"></span> 页'
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
) -> tuple[str, str | None]:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = pdf_path.with_suffix(".pdf.tmp")
    temporary.unlink(missing_ok=True)
    edge_error: Exception | None = None
    if engine in {"edge", "auto"}:
        try:
            _edge_pdf(html_path, temporary)
            temporary.replace(pdf_path)
            return "edge", None
        except Exception as exc:  # pragma: no cover - environment-dependent browser failure
            edge_error = exc
            temporary.unlink(missing_ok=True)
    try:
        _reportlab_pdf(report, evaluation, temporary)
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
                "edition": EDITION_LABELS.get(str(report.get("edition")), report.get("edition")),
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
    cards = "".join(
        '<article><div><span class="date">'
        f'{_escape(row["date"])}</span><span class="edition">{_escape(row["edition"])} · '
        f'r{_escape(row["revision"])}</span></div><h2>{_escape(row["title"])}</h2>'
        '<div class="links">'
        + "".join(
            f'<a href="{_escape(row[key])}">{label}</a>'
            for key, label in (("html", "阅读 HTML"), ("pdf", "打开 PDF"), ("markdown", "Markdown"))
            if row.get(key)
        )
        + (
            f'<span class="score">独立评估 {row["score"]}/45</span>'
            if row.get("score") is not None
            else '<span class="pending">评估中</span>'
        )
        + "</div></article>"
        for row in entries
    )
    if not cards:
        cards = "<p class=empty>尚未生成本地日报。</p>"
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'"><title>Daily Intelligence 日报中心</title><style>:root{{--ink:#17212b;--blue:#234a70;--line:#dfe3e8;--paper:#f3f0e9}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Microsoft YaHei","Noto Sans CJK SC",sans-serif}}header{{background:#18324a;color:white;padding:52px 24px;border-bottom:5px solid #b8812f}}header div,main{{width:min(980px,100%);margin:auto}}h1{{font:700 clamp(34px,6vw,58px) Georgia,serif;margin:0 0 8px}}header p{{color:#dce7ef}}main{{padding:30px 20px 70px}}article{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:22px;margin-bottom:15px;box-shadow:0 8px 20px rgba(20,30,40,.04)}}article>div:first-child{{display:flex;gap:10px;align-items:center}}.date{{font:700 17px Georgia;color:#8a3d32}}.edition{{font-size:13px;color:#657181}}h2{{font-size:20px;margin:8px 0 16px}}.links{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}a{{color:var(--blue);font-weight:700;text-decoration:none;border:1px solid #cbd5df;border-radius:7px;padding:7px 11px}}a:hover{{background:#eef3f7}}.score,.pending{{margin-left:auto;font-size:13px;color:#657181}}.empty{{background:white;padding:30px;border-radius:10px}}</style></head><body><header><div><h1>日报中心</h1><p>本地 HTML 与 PDF 阅读入口；JSON/Markdown 保持事实源。</p></div></header><main>{cards}</main></body></html>"""
    return write_text_atomic(reports_root / "index.html", document)


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
    if "html" in config.formats:
        write_text_atomic(
            html_path,
            render_report_html(
                report,
                evaluation,
                include_pdf_link="pdf" in config.formats,
            ),
        )
        result["html_path"] = str(html_path)
    if "pdf" in config.formats:
        try:
            engine, warning = render_pdf_from_html(
                html_path,
                pdf_path,
                report,
                evaluation,
                config.pdf_engine,
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
