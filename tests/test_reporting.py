import json
from pathlib import Path

from daily_intelligence.reporting import (
    compile_report_data,
    report_content_hash,
    validate_evaluation_data,
    validate_report,
    validate_report_data,
)
from daily_intelligence.reports import render_report_markdown


def _first_report_item(report: dict) -> dict:
    return next(item for section in report["sections"] for item in section["items"])


def test_sample_report_validates():
    root = Path(__file__).resolve().parents[1]
    report = root / "examples" / "sample_report.json"
    errors, _warnings = validate_report(report)
    assert errors == []


def test_schema_rejects_missing_nested_required_field(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    del _first_report_item(report)["why_it_matters"]
    invalid = tmp_path / "invalid-report.json"
    invalid.write_text(json.dumps(report), encoding="utf-8")

    errors, _warnings = validate_report(invalid)

    assert any("why_it_matters" in error for error in errors)


def test_user_facing_report_text_must_be_chinese():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["title"] = "English-only daily report"

    errors, _warnings = validate_report_data(report)

    assert any(
        "title: published user-facing text must contain Chinese" in error for error in errors
    )


def test_not_fetched_index_status_maps_to_metadata_only_report_access():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    ref = _first_report_item(report)["source_refs"][0]
    ref["access"] = "metadata_only"
    index = {"items": [{"item_id": ref["item_id"], "content_status": "not_fetched"}]}

    errors, _warnings = validate_report_data(report, index)

    assert not any("content_status" in error for error in errors)


def test_legacy_report_contract_remains_readable():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report.pop("schema_version")
    report.pop("language")
    report["sections"] = [section for section in report["sections"] if section["items"]]
    for analysis in report["analyses"]:
        for field in ("facts", "reasoning", "scenarios", "actions", "invalidation_signals"):
            analysis.pop(field)

    errors, _warnings = validate_report_data(report)

    assert errors == []


def test_v11_seven_section_report_remains_readable():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["schema_version"] = "1.1"
    market = next(
        section for section in report["sections"] if section["id"] == "information.market"
    )
    market.update(
        {"id": "information.economy", "title": "经济与市场", "category": "economy"}
    )

    errors, _warnings = validate_report_data(report)

    assert errors == []


def test_v12_eight_section_report_remains_readable():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["schema_version"] = "1.2"
    market = next(
        section for section in report["sections"] if section["id"] == "information.market"
    )
    market.update(
        {"id": "information.economy", "title": "经济与市场", "category": "economy"}
    )
    report["sections"].insert(
        4,
        {
            "id": "information.technology",
            "module": "information",
            "category": "technology",
            "title": "科技动态",
            "items": [],
            "coverage_note": "本时段没有达到门槛的科技动态。",
        },
    )

    errors, _warnings = validate_report_data(report)

    assert errors == []


def test_v13_requires_exact_sections_and_titles():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["sections"][0]["title"] = "国际动态"

    errors, _warnings = validate_report_data(report)

    assert any("schema 1.4 requires '国际'" in error for error in errors)


def test_v13_requires_all_judgement_types():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["analyses"][0]["assessment_types"] = ["trend"]

    errors, _warnings = validate_report_data(report)

    assert any("learning_research" in error and "risk" in error for error in errors)


def test_v13_metadata_only_item_must_disclose_unread_body():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    _first_report_item(report)["evidence_notes"] = ["该报道值得继续关注。"]

    errors, _warnings = validate_report_data(report)

    assert any("metadata-only evidence must disclose" in error for error in errors)


def test_v13_evening_requires_changes_and_next_day_watch_items():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["edition"] = "evening"

    errors, _warnings = validate_report_data(report)

    assert any(error.startswith("changes:") for error in errors)
    assert any(error.startswith("tomorrow_watch_items:") for error in errors)

    report["changes"] = ["日间没有出现足以修正晨间判断的新增证据。"]
    report["tomorrow_watch_items"] = ["观察主要实验室是否发布新的智能体评测数据。"]
    errors, _warnings = validate_report_data(report)
    assert errors == []


def test_v11_analysis_requires_reasoning_structure():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["analyses"][0].pop("reasoning")

    errors, _warnings = validate_report_data(report)

    assert any("analyses[0].reasoning" in error for error in errors)


def test_v12_rejects_new_event_reusing_previous_source_item():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    event = _first_report_item(report)
    ref = event["source_refs"][0]
    index = {
        "timezone": "Asia/Shanghai",
        "items": [
            {
                "item_id": ref["item_id"],
                "content_status": ref["access"],
                "published_at": "2026-07-09T15:24:11-04:00",
            }
        ],
    }
    previous = [
        {
            "event_id": "OLDER-EVENT",
            "source_item_ids": [ref["item_id"]],
        }
    ]

    errors, _warnings = validate_report_data(report, index, previous)

    assert any("reuses previously reported source items" in error for error in errors)
    assert any("NEW requires source evidence" in error for error in errors)


def test_v12_caps_freshness_by_source_age():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    event = _first_report_item(report)
    event["status"] = "WATCH"
    ref = event["source_refs"][0]
    index = {
        "timezone": "Asia/Shanghai",
        "items": [
            {
                "item_id": ref["item_id"],
                "content_status": ref["access"],
                "published_at": "2026-07-09T15:24:11-04:00",
            }
        ],
    }

    errors, _warnings = validate_report_data(report, index)

    assert any("age-based cap" in error for error in errors)


def test_v14_limits_each_primary_source_to_fifteen_items():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    section = next(section for section in report["sections"] if section["items"])
    original = section["items"][0]
    section["items"] = []
    event_ids = []
    for position in range(16):
        event = json.loads(json.dumps(original, ensure_ascii=False))
        event["event_id"] = f"TECH-20260712-{position:03d}"
        event["source_refs"][0]["item_id"] = f"item-{position}"
        event_ids.append(event["event_id"])
        section["items"].append(event)
    report["event_count"] = 16
    report["analyses"][0]["evidence_event_ids"] = event_ids

    errors, _warnings = validate_report_data(report)

    assert any("exceed 15 selected items" in error for error in errors)


def test_v14_requires_multi_perspective_analysis_and_majority_event_coverage():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["analyses"][0]["perspectives"] = ["geopolitics"]
    original = _first_report_item(report)
    section = next(section for section in report["sections"] if section["items"])
    for position in range(2):
        event = json.loads(json.dumps(original, ensure_ascii=False))
        event["event_id"] = f"EXTRA-{position}"
        event["source_refs"][0]["item_id"] = f"extra-item-{position}"
        section["items"].append(event)
    report["event_count"] = 3

    errors, _warnings = validate_report_data(report)

    assert any("missing required perspectives" in error for error in errors)
    assert any("cite at least 60%" in error for error in errors)


def test_v14_quality_evaluation_score_must_match_dimensions():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["quality_evaluation"]["total_score"] = 45

    errors, _warnings = validate_report_data(report)

    assert any("must equal dimension score sum" in error for error in errors)


def test_v14_rejected_report_must_exclude_all_continuity():
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["quality_evaluation"]["continuity_decision"] = "reject"
    report["quality_evaluation"]["exclude_from_continuity"] = ["analyses"]

    errors, _warnings = validate_report_data(report)

    assert any("reject requires 'all'" in error for error in errors)


def _v15_report() -> dict:
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))
    report["schema_version"] = "1.5"
    report.pop("quality_evaluation")
    base_analysis = report["analyses"][0]
    report["analyses"] = []
    for position, domain in enumerate(("geopolitics", "ai_technology", "markets"), start=1):
        analysis = json.loads(json.dumps(base_analysis))
        analysis["analysis_id"] = f"ANALYSIS-{position}"
        analysis["domain"] = domain
        report["analyses"].append(analysis)
    for section in report["sections"]:
        section["briefs"] = []
    event = _first_report_item(report)
    section = next(section for section in report["sections"] if section["items"])
    section["briefs"] = [
        {
            "item_id": event["source_refs"][0]["item_id"],
            "title": event["title"],
            "tldr": event["tldr"],
            "importance": event["importance"],
            "status": "NEW",
            "featured_event_id": event["event_id"],
        }
    ]
    return report


def _v15_index(report: dict) -> dict:
    event = _first_report_item(report)
    ref = event["source_refs"][0]
    return {
        "timezone": "Asia/Shanghai",
        "sources": [
            {
                "source_id": "cnbc_world",
                "source_name": "CNBC",
                "source_url": "https://www.cnbc.com/world/",
                "status": "success",
            }
        ],
        "items": [
            {
                "item_id": ref["item_id"],
                "source_id": "cnbc_world",
                "source_name": "CNBC",
                "title": ref["title"],
                "url": ref["url"],
                "content_status": "not_fetched",
                "published_at": f"{report['date']}T01:00:00+08:00",
                "metadata": {"role": "discovery"},
            }
        ],
    }


def test_v15_uses_briefs_for_coverage_and_deterministic_counts():
    report = _v15_report()
    errors, warnings = validate_report_data(report, _v15_index(report))

    assert errors == []
    assert warnings == []
    assert report["brief_count"] == 1
    assert report["event_count"] == 1
    assert report["source_count"] == 1
    assert report["source_metrics"] == {
        "configured": 1,
        "successful": 1,
        "represented": 1,
        "pending": 0,
    }
    assert report["evaluation_status"] == "pending"


def test_v15_compiler_owns_ids_refs_scores_status_and_legacy_evaluation():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    report["quality_evaluation"] = {"total_score": 0}
    event = _first_report_item(report)
    source_item_id = event["source_refs"][0]["item_id"]
    event.pop("event_id")
    event["source_refs"] = [
        {
            "item_id": source_item_id,
            "title": "wrong",
            "url": "https://example.com/wrong",
            "access": "full_text",
        }
    ]
    event["importance_breakdown"] = {"impact": 999}
    for analysis in report["analyses"]:
        analysis.pop("analysis_id")
        analysis["evidence_item_ids"] = [source_item_id]
        analysis["evidence_event_ids"] = []

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    compiled_event = _first_report_item(report)
    assert errors == []
    assert "quality_evaluation" not in report
    assert compiled_event["event_id"].startswith("EVT-")
    assert compiled_event["source_refs"][0]["url"] == index["items"][0]["url"]
    assert sum(compiled_event["importance_breakdown"].values()) == compiled_event["importance"]
    assert all(
        analysis["evidence_event_ids"] == [compiled_event["event_id"]]
        for analysis in report["analyses"]
    )
    brief = next(brief for section in report["sections"] for brief in section["briefs"])
    assert brief["source_rank_label"] == "来源Top1"


def test_v15_compiler_normalizes_mapping_sections_and_legacy_aliases():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    aliases = {
        "information.market": "information.markets",
        "technology.news": "technology.tech_news",
        "technology.open_source": "technology.oss",
    }
    section_mapping = {}
    for section in report["sections"]:
        draft_section = json.loads(json.dumps(section))
        section_id = aliases.get(section["id"], section["id"])
        draft_section.pop("id")
        draft_section.pop("module")
        draft_section.pop("category")
        draft_section.pop("title")
        section_mapping[section_id] = draft_section
    report["sections"] = section_mapping

    warnings = compile_report_data(report, index)
    errors, _validation_warnings = validate_report_data(report, index)

    assert errors == []
    assert [section["id"] for section in report["sections"]] == [
        "information.international",
        "information.domestic",
        "information.military",
        "information.market",
        "technology.news",
        "technology.papers",
        "technology.open_source",
    ]
    assert any("technology.tech_news" in warning for warning in warnings)
    assert _first_report_item(report)["title"]


def test_v15_featured_event_keeps_cross_source_articles_as_separate_events():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    event = _first_report_item(report)
    first_item_id = event["source_refs"][0]["item_id"]
    second_item = json.loads(json.dumps(index["items"][0]))
    second_item.update(
        {
            "item_id": "bbc_world-cross-source",
            "source_id": "bbc_world",
            "source_name": "BBC",
            "title": "A second source confirms the same event",
            "url": "https://www.bbc.com/news/example-cross-source",
        }
    )
    index["sources"].append(
        {
            "source_id": "bbc_world",
            "source_name": "BBC",
            "source_url": "https://www.bbc.com/news",
            "status": "success",
        }
    )
    index["items"].append(second_item)
    event.pop("source_refs")
    event["source_item_ids"] = [first_item_id, second_item["item_id"]]

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    compiled = _first_report_item(report)
    assert any("require exactly one source item" in error for error in errors)
    assert compiled["primary_source"]["id"] == "cnbc_world"
    assert [ref["item_id"] for ref in compiled["source_refs"]] == [
        first_item_id,
        second_item["item_id"],
    ]


def test_v15_featured_event_rejects_multiple_articles_from_same_publisher():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    event = _first_report_item(report)
    first_item_id = event["source_refs"][0]["item_id"]
    second_item = json.loads(json.dumps(index["items"][0]))
    second_item.update(
        {
            "item_id": "cnbc_world-unrelated-article",
            "title": "An unrelated article from the same publisher",
            "url": "https://www.cnbc.com/2026/07/12/unrelated.html",
        }
    )
    index["items"].append(second_item)
    event.pop("source_refs")
    event["source_item_ids"] = [first_item_id, second_item["item_id"]]

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    assert any("require exactly one source item" in error for error in errors)


def test_v15_compiler_omits_brief_only_analysis_evidence_with_warning():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    event = _first_report_item(report)
    featured_item_id = event["source_refs"][0]["item_id"]
    for analysis in report["analyses"]:
        analysis["evidence_item_ids"] = [featured_item_id, "brief-only-item"]
        analysis["evidence_event_ids"] = []

    warnings = compile_report_data(report, index)
    errors, _validation_warnings = validate_report_data(report, index)

    compiled_event_id = _first_report_item(report)["event_id"]
    assert errors == []
    assert all(
        analysis["evidence_event_ids"] == [compiled_event_id]
        for analysis in report["analyses"]
    )
    assert any("brief-only-item" in warning for warning in warnings)


def test_v15_compiler_relocates_brief_to_its_indexed_section():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    event_section = next(section for section in report["sections"] if section["items"])
    target_section = next(
        section for section in report["sections"] if section["id"] == "information.domestic"
    )
    index["items"][0].update({"module": "technology", "category": "news"})
    target_section["briefs"].append(event_section["briefs"].pop())

    warnings = compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    event_id = _first_report_item(report)["event_id"]
    linked_brief = next(
        brief
        for section in report["sections"]
        for brief in section["briefs"]
        if brief.get("featured_event_id") == event_id
    )
    assert errors == []
    assert linked_brief["item_id"] == index["items"][0]["item_id"]
    canonical_section = next(
        section for section in report["sections"] if linked_brief in section["briefs"]
    )
    assert canonical_section["id"] == "technology.news"
    assert any("moved brief" in warning for warning in warnings)


def test_v15_compiler_omits_unknown_brief_ids_with_actionable_warning():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    section = next(section for section in report["sections"] if section["briefs"])
    unknown = json.loads(json.dumps(section["briefs"][0]))
    unknown.update({"item_id": "github_trending-", "featured_event_id": None})
    section["briefs"].append(unknown)

    warnings = compile_report_data(report, index)

    assert all(
        brief["item_id"] != "github_trending-"
        for candidate_section in report["sections"]
        for brief in candidate_section["briefs"]
    )
    assert any(
        "github_trending-" in warning and "context.brief_plan" in warning
        for warning in warnings
    )


def test_v15_compiler_appends_required_metadata_only_disclosure():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    event = _first_report_item(report)
    event["evidence_notes"] = ["该事项仍需持续观察。"]

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    assert errors == []
    assert any("未读取正文" in note for note in event["evidence_notes"])


def test_v15_compiler_normalizes_simple_top_level_draft_fields():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    report.pop("title")
    report["executive_summary"] = "这是本版的中文摘要。"

    warnings = compile_report_data(report, index)
    errors, _validation_warnings = validate_report_data(report, index)

    assert errors == []
    assert report["title"].startswith("每日情报")
    assert report["executive_summary"] == ["这是本版的中文摘要。"]
    assert any("executive_summary string" in warning for warning in warnings)


def test_v15_compiler_never_creates_missing_semantic_briefs():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    index["source_policies"] = {
        "cnbc_world": {"report_target": 3, "report_max": 15}
    }
    base = index["items"][0]
    source_section = next(section for section in report["sections"] if section["briefs"])
    base["module"] = source_section["module"]
    base["category"] = source_section["category"]
    for position in (2, 3):
        item = json.loads(json.dumps(base))
        item["item_id"] = f"cnbc-{position}"
        item["title"] = f"Source metadata headline {position}"
        item["url"] = f"https://www.cnbc.com/example-{position}/"
        item["published_at"] = None
        item["metadata"]["source_rank"] = position
        index["items"].append(item)

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    section = next(section for section in report["sections"] if section["briefs"])
    assert len(section["briefs"]) == 1
    assert {brief["item_id"] for brief in section["briefs"]} == {base["item_id"]}
    assert any(
        "coverage source 'cnbc_world': selected 1 of required 3" in error
        for error in errors
    )


def test_v15_flat_importance_uses_source_rank_as_non_blocking_tie_breaker():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    index["source_policies"] = {
        "cnbc_world": {"report_target": 3, "report_max": 15}
    }
    section = next(section for section in report["sections"] if section["briefs"])
    base_brief = section["briefs"][0]
    base_item = index["items"][0]
    base_item["module"] = section["module"]
    base_item["category"] = section["category"]
    for position in (2, 3):
        item = json.loads(json.dumps(base_item))
        item.update(
            {
                "item_id": f"cnbc-{position}",
                "title": f"Source headline {position}",
                "url": f"https://www.cnbc.com/example-{position}/",
            }
        )
        item["metadata"]["source_rank"] = position
        index["items"].append(item)
        brief = json.loads(json.dumps(base_brief))
        brief.update(
            {
                "item_id": item["item_id"],
                "title": f"来源标题 {position}",
                "importance": base_brief["importance"],
            }
        )
        brief.pop("featured_event_id", None)
        section["briefs"].append(brief)

    compile_report_data(report, index)
    errors, warnings = validate_report_data(report, index)

    assert errors == []
    assert any("source_rank is used" in warning for warning in warnings)


def test_v15_rejects_language_markers_and_fake_tldr_when_full_text_exists():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    index["items"][0]["content_status"] = "full_text"
    index["items"][0]["title"] = (
        "Five arrested after raid on two Hong Kong independent bookshops"
    )
    brief = next(brief for section in report["sections"] for brief in section["briefs"])
    brief["title"] = "[英] Five arrested after raid on two Hong Kong independent bookshops"
    brief.pop("title_zh", None)
    brief["tldr"] = (
        "来源原文标题：Five arrested after raid on two Hong Kong independent bookshops。"
        "该条目暂未获取中文摘要，仅依据标题和元数据记录。"
    )

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    assert brief["title"] == index["items"][0]["title"]
    assert not brief["title"].startswith("[英]")
    assert any("title_zh" in error and "item_id=" in error for error in errors)
    assert any("boilerplate" in error for error in errors)
    assert any("index contains full_text" in error for error in errors)


def test_v15_rejects_foreign_prefix_and_link_only_tldr_workarounds():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    index["items"][0]["title"] = (
        "Uganda calls for travel restrictions to be lifted after last outbreak"
    )
    brief = next(brief for section in report["sections"] for brief in section["briefs"])
    brief["title"] = index["items"][0]["title"]
    brief["title_zh"] = "【外文】Uganda calls for travel restrictions to be lifted"
    brief["tldr"] = "详见原文链接。Uganda calls for travel restrictions to be lifted."

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    compiled_brief = next(
        candidate
        for section in report["sections"]
        for candidate in section["briefs"]
        if candidate["item_id"] == brief["item_id"]
    )
    assert "title_zh" not in compiled_brief
    assert any("title_zh" in error and brief["item_id"] in error for error in errors)
    assert any("boilerplate" in error and brief["item_id"] in error for error in errors)


def test_v15_rejects_source_reported_tldr_placeholder():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    brief = next(brief for section in report["sections"] for brief in section["briefs"])
    brief["tldr"] = "来源The Guardian UK报道。"

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    assert any("boilerplate" in error and brief["item_id"] in error for error in errors)


def test_v15_rejects_metadata_access_disclaimer_as_tldr():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    brief = next(brief for section in report["sections"] for brief in section["briefs"])
    brief["tldr"] = (
        "仅取得来源标题或公开元数据，正文尚未读取；请通过原文链接查看完整内容。"
    )

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    assert any("boilerplate" in error and brief["item_id"] in error for error in errors)


def test_v15_accepts_model_authored_translation_and_title_level_summary():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    index["items"][0]["title"] = (
        "Five arrested after raid on two Hong Kong independent bookshops"
    )
    brief = next(brief for section in report["sections"] for brief in section["briefs"])
    brief["title_zh"] = "两家香港独立书店遭突袭后五人被捕"
    brief["tldr"] = "两家香港独立书店遭到突袭，事件导致五人被捕。"

    compile_report_data(report, index)
    errors, _warnings = validate_report_data(report, index)

    assert errors == []
    assert brief["title_zh"] == "两家香港独立书店遭突袭后五人被捕"


def test_v15_compiler_deduplicates_briefs_and_rejects_future_new_status():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    item_id = index["items"][0]["item_id"]
    index["items"][0]["published_at"] = "2099-01-01T00:00:00+08:00"
    source_section = next(section for section in report["sections"] if section["briefs"])
    duplicate = json.loads(json.dumps(source_section["briefs"][0]))
    target_section = next(
        section for section in report["sections"] if section is not source_section
    )
    target_section["briefs"].append(duplicate)

    compile_report_data(report, index)

    briefs = [brief for section in report["sections"] for brief in section["briefs"]]
    assert sum(brief["item_id"] == item_id for brief in briefs) == 1
    assert briefs[0]["status"] == "WATCH"


def test_v15_render_hides_numeric_importance_and_access_state():
    report = _v15_report()
    index = _v15_index(report)
    index["date"] = report["date"]
    index["edition"] = report["edition"]
    compile_report_data(report, index)

    rendered = render_report_markdown(report)

    assert "**重要性：**" not in rendered
    assert "**原文状态：**" not in rendered
    assert "来源Top1" in rendered
    assert "**中文标题：**" in rendered
    assert "### 从地缘政治专家的角度" in rendered
    assert "### 从 AI 研究/开发工程师的角度" in rendered
    assert "### 从股票分析师的角度" in rendered


def test_v15_caps_featured_events_and_keeps_remaining_stories_as_briefs():
    report = _v15_report()
    index = _v15_index(report)
    section = next(section for section in report["sections"] if section["items"])
    base_event = section["items"][0]
    base_brief = section["briefs"][0]
    base_index_item = index["items"][0]
    section["items"] = []
    section["briefs"] = []
    index["items"] = []
    event_ids = []
    for position in range(13):
        event = json.loads(json.dumps(base_event))
        brief = json.loads(json.dumps(base_brief))
        index_item = json.loads(json.dumps(base_index_item))
        event_id = f"TECH-20260712-{position:03d}"
        item_id = f"cnbc_world-example-{position}"
        url = f"https://www.cnbc.com/2026/07/12/example-{position}.html"
        event["event_id"] = event_id
        event["source_refs"][0].update({"item_id": item_id, "url": url})
        brief.update({"item_id": item_id, "featured_event_id": event_id})
        index_item.update({"item_id": item_id, "url": url})
        section["items"].append(event)
        section["briefs"].append(brief)
        index["items"].append(index_item)
        event_ids.append(event_id)
    report["analyses"][0]["evidence_event_ids"] = event_ids

    errors, _warnings = validate_report_data(report, index)

    assert any("at most 12 featured events" in error for error in errors)


def test_v15_rejects_new_without_date_and_source_url_identity_mismatch():
    report = _v15_report()
    index = _v15_index(report)
    index["items"][0]["published_at"] = None
    _first_report_item(report)["source_refs"][0]["url"] = "https://example.com/invented"

    errors, _warnings = validate_report_data(report, index)

    assert any("NEW requires a parseable" in error for error in errors)
    assert any("URL does not match indexed item URL" in error for error in errors)


def test_post_publication_evaluation_is_hash_bound_and_independent():
    report = _v15_report()
    report["report_id"] = "daily-2026-07-12-morning-r1"
    evaluation = {
        "evaluator_role": "independent",
        "evaluated_report_id": report["report_id"],
        "evaluated_content_hash": report_content_hash(report),
        "dimensions": [
            {"id": dimension, "score": 4, "finding": "该维度整体合格，仍可继续改进。"}
            for dimension in sorted(
                {
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
            )
        ],
        "total_score": 36,
        "main_defects": ["部分来源覆盖仍然不足。"],
        "insufficient_evidence": ["个别结论缺少独立交叉证据。"],
        "improvements": ["下一版优先补充高价值来源。"],
        "continuity_decision": "selective",
        "exclude_from_continuity": ["event_summaries"],
    }

    assert validate_evaluation_data(evaluation, report) == []
    evaluation["evaluated_content_hash"] = "0" * 64
    assert any("content_hash" in error for error in validate_evaluation_data(evaluation, report))


def test_low_quality_evaluation_cannot_accept_continuity():
    report = _v15_report()
    report["report_id"] = "daily-2026-07-16-evening-r1"
    score_by_dimension = {
        "coverage": 3,
        "importance_ordering": 3,
        "historical_continuity": 3,
        "timeliness": 3,
        "factual_reliability": 2,
        "summary_accuracy": 2,
        "analysis_traceability": 2,
        "readability": 2,
        "compliance_boundaries": 2,
    }
    evaluation = {
        "evaluator_role": "independent",
        "evaluated_report_id": report["report_id"],
        "evaluated_content_hash": report_content_hash(report),
        "dimensions": [
            {"id": dimension, "score": score, "finding": "该维度存在严重质量问题。"}
            for dimension, score in score_by_dimension.items()
        ],
        "total_score": 22,
        "main_defects": ["摘要与证据质量不足。"],
        "insufficient_evidence": ["多数条目缺少可验证正文。"],
        "improvements": ["重新生成自然中文摘要。"],
        "continuity_decision": "accept",
        "exclude_from_continuity": [],
    }

    errors = validate_evaluation_data(evaluation, report)

    assert any("reject all content" in error for error in errors)
    evaluation["continuity_decision"] = "reject"
    evaluation["exclude_from_continuity"] = ["all"]
    assert validate_evaluation_data(evaluation, report) == []


def test_v15_rejects_named_sources_that_are_not_bound_to_event_evidence():
    report = _v15_report()
    index = _v15_index(report)
    index["sources"].append(
        {
            "source_id": "bbc_world",
            "source_name": "BBC",
            "source_url": "https://www.bbc.com/news",
            "status": "success",
        }
    )
    _first_report_item(report)["evidence_notes"].append("BBC也独立确认了这一判断。")
    report["analyses"][0]["facts"].append("BBC报道称该趋势已经得到确认。")

    errors, _warnings = validate_report_data(report, index)

    assert any("evidence_notes names sources not bound" in error for error in errors)
    assert any("facts names sources not represented" in error for error in errors)


def test_v15_numeric_scenarios_require_an_explicit_basis():
    report = _v15_report()
    index = _v15_index(report)
    report["analyses"][0]["scenarios"] = ["上涨情景概率为55%，价格区间为90至110美元。"]

    errors, _warnings = validate_report_data(report, index)

    assert any("scenario_basis" in error for error in errors)
    report["analyses"][0]["scenario_basis"] = "以上数字仅用于情景推演，并非确定性预测。"
    errors, _warnings = validate_report_data(report, index)
    assert not any("scenario_basis" in error for error in errors)
