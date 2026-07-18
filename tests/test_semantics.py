from pathlib import Path

from daily_intelligence.config import load_config
from daily_intelligence.context import build_context
from daily_intelligence.reporting import compile_report_data
from daily_intelligence.semantics import (
    finalize_semantic_cache_evaluation,
    load_semantic_cache,
    semantic_fingerprint,
    update_semantic_cache_from_report,
)
from daily_intelligence.utils import read_json, write_json


def _item(description: str = "公开摘要") -> dict:
    return {
        "item_id": "bbc_world-cache",
        "source_id": "bbc_world",
        "source_name": "BBC",
        "title": "A sufficiently detailed English headline",
        "url": "https://www.bbc.com/news/articles/cache",
        "canonical_url": "https://bbc.com/news/articles/cache",
        "description": description,
        "published_at": "2026-07-17T05:00:00+08:00",
        "discovered_at": "2026-07-17T05:30:00+08:00",
        "module": "information",
        "category": "international",
        "content_status": "not_fetched",
        "metadata": {"source_rank": 1, "role": "evidence"},
    }


def _brief() -> dict:
    return {
        "item_id": "bbc_world-cache",
        "title": "A sufficiently detailed English headline",
        "title_zh": "一条足够具体的英文新闻标题",
        "tldr": "这是一条基于公开摘要撰写的中文简要说明。",
        "importance": 70,
        "status": "NEW",
    }


def _approve(report_id: str, data_dir: Path) -> None:
    finalize_semantic_cache_evaluation(
        {
            "evaluation_id": "evaluation-1",
            "evaluated_report_id": report_id,
            "continuity_decision": "accept",
            "dimensions": [
                {"id": "summary_accuracy", "score": 4},
                {"id": "factual_reliability", "score": 4},
                {"id": "compliance_boundaries", "score": 4},
            ],
        },
        data_dir,
    )


def test_semantic_fingerprint_invalidates_when_evidence_changes():
    original = semantic_fingerprint(_item("摘要A"))

    assert semantic_fingerprint(_item("摘要A")) == original
    assert semantic_fingerprint(_item("摘要B")) != original


def test_evaluated_semantics_are_reused_in_context_and_compiler(tmp_path: Path):
    data_dir = tmp_path / "data"
    item = _item()
    report_id = "daily-2026-07-17-morning-r1"
    report = {
        "report_id": report_id,
        "generated_at": "2026-07-17T06:00:00+08:00",
        "sections": [{"briefs": [_brief()]}],
    }
    index = {
        "date": "2026-07-17",
        "edition": "evening",
        "timezone": "Asia/Shanghai",
        "source_policies": {"bbc_world": {"report_target": 1, "report_max": 15}},
        "sources": [
            {
                "source_id": "bbc_world",
                "source_name": "BBC",
                "source_url": "https://www.bbc.com/news",
                "status": "success",
            }
        ],
        "items": [item],
    }
    update_semantic_cache_from_report(report, index, data_dir)
    _approve(report_id, data_dir)
    index_path = write_json(
        data_dir / "indexes" / "2026-07-17" / "evening-r1.json", index
    )

    context = read_json(build_context(index_path, load_config(), data_dir, "evening"))

    assert context["reusable_briefs"][0]["tldr"] == _brief()["tldr"]
    assert context["brief_plan"][0]["reuse_item_ids"] == [item["item_id"]]
    assert context["brief_plan"][0]["author_item_ids"] == []
    assert context["brief_authoring_batches"] == []

    draft = {"sections": [], "analyses": []}
    warnings = compile_report_data(draft, index, load_semantic_cache(data_dir))
    compiled = next(
        brief
        for section in draft["sections"]
        for brief in section["briefs"]
        if brief["item_id"] == item["item_id"]
    )
    assert compiled["semantic_cache_reused"] is True
    assert any("reused approved semantic cache" in warning for warning in warnings)


def test_low_reliability_evaluation_rejects_semantic_cache(tmp_path: Path):
    data_dir = tmp_path / "data"
    report_id = "daily-low-quality"
    update_semantic_cache_from_report(
        {
            "report_id": report_id,
            "generated_at": "2026-07-17T06:00:00+08:00",
            "sections": [{"briefs": [_brief()]}],
        },
        {"items": [_item()]},
        data_dir,
    )

    finalize_semantic_cache_evaluation(
        {
            "evaluated_report_id": report_id,
            "continuity_decision": "selective",
            "dimensions": [
                {"id": "summary_accuracy", "score": 4},
                {"id": "factual_reliability", "score": 2},
                {"id": "compliance_boundaries", "score": 4},
            ],
        },
        data_dir,
    )

    assert load_semantic_cache(data_dir)[_item()["item_id"]]["state"] == "rejected"


def test_selective_evaluation_excluding_summaries_rejects_semantic_cache(
    tmp_path: Path,
):
    data_dir = tmp_path / "data"
    report_id = "daily-excluded-summaries"
    update_semantic_cache_from_report(
        {
            "report_id": report_id,
            "generated_at": "2026-07-17T06:00:00+08:00",
            "sections": [{"briefs": [_brief()]}],
        },
        {"items": [_item()]},
        data_dir,
    )

    finalize_semantic_cache_evaluation(
        {
            "evaluated_report_id": report_id,
            "continuity_decision": "selective",
            "exclude_from_continuity": ["event_summaries"],
            "dimensions": [
                {"id": "summary_accuracy", "score": 5},
                {"id": "factual_reliability", "score": 5},
                {"id": "compliance_boundaries", "score": 5},
            ],
        },
        data_dir,
    )

    assert load_semantic_cache(data_dir)[_item()["item_id"]]["state"] == "rejected"


def test_rewritten_semantics_return_to_pending_until_re_evaluated(tmp_path: Path):
    data_dir = tmp_path / "data"
    report_id = "daily-original"
    report = {
        "report_id": report_id,
        "generated_at": "2026-07-17T06:00:00+08:00",
        "sections": [{"briefs": [_brief()]}],
    }
    index = {"items": [_item()]}
    update_semantic_cache_from_report(report, index, data_dir)
    _approve(report_id, data_dir)

    rewritten = _brief()
    rewritten["tldr"] = "这是经过模型改写、尚待新评估确认的中文摘要。"
    update_semantic_cache_from_report(
        {
            "report_id": "daily-rewritten",
            "generated_at": "2026-07-17T18:00:00+08:00",
            "sections": [{"briefs": [rewritten]}],
        },
        index,
        data_dir,
    )

    entry = load_semantic_cache(data_dir)[_item()["item_id"]]
    assert entry["state"] == "pending"
    assert entry["report_id"] == "daily-rewritten"
