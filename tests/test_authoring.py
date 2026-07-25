from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from daily_intelligence.authoring import validate_authoring_batch
from daily_intelligence.config import load_config
from daily_intelligence.context import build_context
from daily_intelligence.utils import read_json, write_json
from daily_intelligence.workflow import (
    RunStatus,
    accept_authoring_batch,
    accept_authoring_metrics,
    assemble_authoring,
    begin_authoring,
    get_authoring_status,
    prepare_authoring_analysis,
)


def _authoring_run(tmp_path: Path, item_count: int = 2) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    date = "2026-07-25"
    items = [
        {
            "item_id": f"bbc-{position}",
            "source_id": "bbc_world",
            "source_name": "BBC",
            "title": f"Public headline {position}",
            "description": f"Public description {position}",
            "url": f"https://www.bbc.com/news/articles/{position}",
            "published_at": f"{date}T01:0{position}:00+08:00",
            "discovered_at": f"{date}T01:1{position}:00+08:00",
            "module": "information",
            "category": "international",
            "content_status": "not_fetched",
            "metadata": {"source_rank": position + 1},
        }
        for position in range(item_count)
    ]
    index_path = write_json(
        data_dir / "indexes" / date / "morning-r1.json",
        {
            "date": date,
            "edition": "morning",
            "timezone": "Asia/Shanghai",
            "sources": [
                {
                    "source_id": "bbc_world",
                    "source_name": "BBC",
                    "source_url": "https://www.bbc.com/news",
                    "status": "success",
                }
            ],
            "items": items,
            "source_policies": {
                "bbc_world": {"report_target": item_count, "report_max": 15}
            },
        },
    )
    context_path = build_context(
        index_path,
        load_config(),
        data_dir,
        "morning",
    )
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    run_path = write_json(
        data_dir / "runs" / date / "morning.json",
        {
            "schema_version": "1.0",
            "run_id": f"run-{date}-morning",
            "data_root": str(data_dir.resolve()),
            "date": date,
            "edition": "morning",
            "timezone": "Asia/Shanghai",
            "status": RunStatus.AWAITING_AUTHORING,
            "created_at": now.isoformat(timespec="seconds"),
            "updated_at": now.isoformat(timespec="seconds"),
            "deadline_at": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
            "artifacts": {
                "index_path": str(index_path),
                "context_path": str(context_path),
            },
            "pending_sources": [],
        },
    )
    return data_dir, run_path


def _valid_batch_payload(packet: dict) -> dict:
    return {
        "briefs": [
            {
                "item_id": item_id,
                "title": candidate["title"],
                "title_zh": f"公开新闻标题 {position}",
                "tldr": f"这是第 {position} 条公开新闻的中文事实摘要。",
                "importance": 80 - position,
                "status": "NEW",
            }
            for position, item_id in enumerate(packet["author_item_ids"], start=1)
            for candidate in packet["candidates"]
            if candidate["item_id"] == item_id
        ]
    }


def test_english_authoring_batch_requires_english_translation_and_summary():
    packet = {
        "output_language": "en",
        "author_item_ids": ["cn-1"],
        "candidates": [
            {
                "item_id": "cn-1",
                "title": "一条中文新闻标题",
                "source_language": "zh-CN",
            }
        ],
    }
    payload = {
        "briefs": [
            {
                "item_id": "cn-1",
                "title": "一条中文新闻标题",
                "title_en": "A Chinese-language public news headline",
                "tldr": "This is a substantive English summary of the observed facts.",
                "importance": 70,
                "status": "NEW",
            }
        ]
    }

    assert validate_authoring_batch(packet, payload) == []

    payload["briefs"][0]["tldr"] = "这是一条中文摘要。"
    errors = validate_authoring_batch(packet, payload)
    assert any("substantive English summary" in error for error in errors)


def test_authoring_batches_are_validated_and_python_assembled(tmp_path: Path):
    data_dir, run_path = _authoring_run(tmp_path)

    begin_authoring(run_path, data_dir)
    run = read_json(run_path)
    context = read_json(Path(run["artifacts"]["context_path"]))
    batch = context["brief_authoring_batches"][0]
    packet = read_json(Path(batch["packet_path"]))
    draft_path = write_json(
        Path(packet["draft_result_path"]),
        _valid_batch_payload(packet),
    )
    accepted = accept_authoring_batch(
        run_path,
        batch["batch_id"],
        draft_path,
        data_dir,
    )

    receipt = read_json(accepted)
    assert receipt["brief_count"] == 2
    assert receipt["duration_seconds"] >= 0
    status = get_authoring_status(run_path, data_dir)
    assert status["completed_batches"] == status["expected_batches"] == 1

    prepare_authoring_analysis(run_path, data_dir)
    run = read_json(run_path)
    authoring = run["artifacts"]["authoring"]
    packet = read_json(Path(authoring["analysis_packet_path"]))
    assert len(packet["candidate_events"]) == 2
    analysis_path = write_json(
        Path(authoring["analysis_result_path"]),
        {
            "title": "每日情报早报 — 2026年7月25日",
            "executive_summary": ["本版重点来自确定性合并后的新闻与研判。"],
            "featured_events": [
                {
                    "section_id": "information.international",
                    "title": "重点事件",
                    "tldr": "该事件用于验证研判装配流程。",
                    "why_it_matters": "它检验模型语义与确定性结构的职责边界。",
                    "importance": 80,
                    "importance_reason": "具有测试价值。",
                    "confidence": 0.7,
                    "status": "NEW",
                    "source_item_ids": ["bbc-0"],
                    "evidence_notes": [],
                    "tags": [],
                }
            ],
            "analyses": [],
            "cross_perspective_synthesis": {},
        },
    )
    assemble_authoring(run_path, analysis_path, data_dir)

    run = read_json(run_path)
    report = read_json(Path(run["artifacts"]["authoring"]["report_draft_path"]))
    assert report["schema_version"] == "2.0"
    assert sum(len(section["briefs"]) for section in report["sections"]) == 2
    assert report["sections"][0]["items"][0]["source_item_ids"] == ["bbc-0"]
    assert run["artifacts"]["authoring"]["metrics"]["brief_assembly_seconds"] >= 0


def test_authoring_batch_rejects_missing_assigned_items(tmp_path: Path):
    data_dir, run_path = _authoring_run(tmp_path)
    begin_authoring(run_path, data_dir)
    run = read_json(run_path)
    context = read_json(Path(run["artifacts"]["context_path"]))
    batch = context["brief_authoring_batches"][0]
    packet = read_json(Path(batch["packet_path"]))
    payload = _valid_batch_payload(packet)
    payload["briefs"].pop()
    draft_path = write_json(Path(packet["draft_result_path"]), payload)

    with pytest.raises(ValueError, match="missing assigned item IDs"):
        accept_authoring_batch(
            run_path,
            batch["batch_id"],
            draft_path,
            data_dir,
        )

    assert not Path(packet["accepted_result_path"]).exists()


def test_authoring_metrics_keep_only_bounded_delegate_observability(tmp_path: Path):
    data_dir, run_path = _authoring_run(tmp_path)
    begin_authoring(run_path, data_dir)
    run = read_json(run_path)
    session = read_json(Path(run["artifacts"]["authoring"]["session_path"]))
    metrics_draft = write_json(
        Path(session["paths"]["delegation_metrics_draft"]),
        {
            "results": [
                {
                    "task_index": 0,
                    "status": "completed",
                    "summary": "This potentially large child response is not retained.",
                    "tool_trace": [{"tool": "terminal"}],
                    "api_calls": 4,
                    "duration_seconds": 12.5,
                    "model": "fast-model",
                    "exit_reason": "completed",
                    "tokens": {"input": 1200, "output": 340},
                }
            ],
            "total_duration_seconds": 12.8,
        },
    )

    accepted_path = accept_authoring_metrics(run_path, metrics_draft, data_dir)
    accepted = read_json(accepted_path)

    assert accepted["totals"] == {
        "wall_seconds": 12.8,
        "child_compute_seconds": 12.5,
        "api_calls": 4,
        "input_tokens": 1200,
        "output_tokens": 340,
    }
    assert accepted["batch_metrics"][0]["batch_id"] == "brief-batch-1"
    assert "summary" not in accepted["batch_metrics"][0]
    assert "tool_trace" not in accepted["batch_metrics"][0]
    status = get_authoring_status(run_path, data_dir)
    assert status["batches"][0]["duration_source"] == "delegate_result"
    assert status["batches"][0]["output_tokens"] == 340


def test_authoring_session_rejects_context_change_after_dispatch(tmp_path: Path):
    data_dir, run_path = _authoring_run(tmp_path)
    begin_authoring(run_path, data_dir)
    run = read_json(run_path)
    context_path = Path(run["artifacts"]["context_path"])
    context = read_json(context_path)
    context["context_warnings"] = ["context changed after workers were dispatched"]
    write_json(context_path, context)

    with pytest.raises(RuntimeError, match="context changed after dispatch"):
        begin_authoring(run_path, data_dir)
