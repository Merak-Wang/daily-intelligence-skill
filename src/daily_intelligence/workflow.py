from __future__ import annotations

import os
import re
import subprocess
from dataclasses import replace
from datetime import datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from .authoring import (
    assemble_report_draft,
    begin_authoring_session,
    prepare_analysis_packet,
    record_authoring_metrics,
    submit_authoring_batch,
)
from .authoring import (
    authoring_status as inspect_authoring_status,
)
from .collector import collect_sources
from .config import AppConfig, MediaConfig, OutputConfig, project_root
from .content import extract_content
from .context import build_context
from .local_output import write_local_outputs
from .localization import localized
from .media import prefetch_index_images
from .monitor import fresh_monitor_snapshot_path, refresh_monitor
from .notion import publish_report, sync_user_feedback
from .reports import save_report
from .runtime import require_data_root_path, validate_run_data_root
from .storage import exclusive_lock
from .utils import now_iso, read_json, today_str, write_json


class RunStatus(StrEnum):
    CREATED = "created"
    COLLECTING = "collecting"
    BUILDING_CONTEXT = "building_context"
    AWAITING_SELECTION = "awaiting_selection"
    EXTRACTING_CONTENT = "extracting_content"
    AWAITING_AUTHORING = "awaiting_authoring"
    FINALIZING = "finalizing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    COMPLETED_PARTIAL = "completed_partial"
    FAILED = "failed"


TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.COMPLETED_PARTIAL,
    RunStatus.FAILED,
}


def schedule_independent_evaluation(
    report_path: Path,
    index_path: Path,
    data_dir: Path,
    report_id: str,
    content_hash: str,
    publish_notion: bool = False,
) -> dict[str, Any]:
    """Create a bounded evaluator job after local delivery; remote publishing is optional."""
    draft_path = data_dir / "evaluations" / "drafts" / f"{report_id}.json"
    notion_flag = " --publish" if publish_notion else ""
    report = read_json(report_path) if report_path.is_file() else None
    language = (
        report.get("language")
        if isinstance(report, dict)
        else "zh-CN"
    ) or "zh-CN"
    prompt = localized(
        language,
        (
            "你是发布后独立评估 Agent，不参与日报生成，也不得修改主报告。"
            f"只读不可变报告 {report_path}、索引 {index_path} 和技能中的 "
            "templates/report-contract.md；按九个固定维度各给 1—5 分，总分必须等于"
            "九项之和，简洁指出主要缺陷、证据不足和改进建议。"
            f"被评报告 ID 是 {report_id}，内容 SHA-256 是 {content_hash}。"
            f"把评估 JSON 写到 {draft_path}，然后执行：daily-intel --data-dir "
            f"\"{data_dir}\" finalize-evaluation --report \"{report_path}\" "
            f"--evaluation \"{draft_path}\" {notion_flag}。若该 report_id 已有 "
            "completed 评估则直接退出；不得要求用户点击。该任务最多由调度器尝试"
            "三次，以容忍临时模型/API 连接失败。"
        ),
        (
            "You are an independent post-publication evaluator. You did not author the "
            "report and must not modify it. Read only the immutable report "
            f"{report_path}, index {index_path}, and templates/report-contract.md. "
            "Score each of the nine fixed dimensions from 1 to 5; total_score must equal "
            "their sum. Write concise findings, main defects, evidence gaps, and "
            f"improvements in English. The report ID is {report_id}; its SHA-256 is "
            f"{content_hash}. Write the evaluation JSON to {draft_path}, then run: "
            f"daily-intel --data-dir \"{data_dir}\" finalize-evaluation --report "
            f"\"{report_path}\" --evaluation \"{draft_path}\" {notion_flag}. Exit if a "
            "completed evaluation already exists for this report_id. Do not ask the user "
            "to click anything. The scheduler may attempt this job up to three times to "
            "tolerate temporary model or API failures."
        ),
    )
    command = [
        "hermes",
        "cron",
        "create",
        "2m",
        prompt,
        "--repeat",
        "3",
        "--skill",
        "daily-intelligence",
        "--name",
        f"Daily Intelligence Evaluation {report_id}",
        "--deliver",
        "local",
        "--workdir",
        str(project_root()),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        return {"status": "schedule_failed", "error": f"{type(exc).__name__}: {exc}"}
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        return {"status": "schedule_failed", "error": detail or "hermes cron create failed"}
    detail = completed.stdout.strip()
    match = re.search(r"Created job:\s*([A-Za-z0-9_-]+)", detail)
    return {
        "status": "scheduled",
        "detail": detail,
        "attempts": 3,
        "interval": "2m",
        **({"job_id": match.group(1)} if match else {}),
    }


def edition_window(date_value: str, edition: str, timezone: str) -> dict[str, str]:
    zone = ZoneInfo(timezone)
    date = datetime.fromisoformat(date_value).date()
    if edition == "morning":
        start = datetime.combine(date - timedelta(days=1), time(18, 0), zone)
        end = datetime.combine(date, time(6, 0), zone)
    elif edition == "evening":
        start = datetime.combine(date, time(6, 0), zone)
        end = datetime.combine(date, time(18, 0), zone)
    else:
        raise ValueError(f"Unknown edition: {edition}")
    return {"start": start.isoformat(), "end": end.isoformat()}


def _update_run(path: Path, run: dict[str, Any], status: RunStatus, **fields: Any) -> None:
    timestamp = fields.get("updated_at") or datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    previous_status = str(run.get("status") or "")
    run.update(fields)
    run["status"] = status
    run["updated_at"] = timestamp
    if previous_status != status.value:
        run.setdefault("stage_timestamps", {})[status.value] = timestamp
        run.setdefault("stage_history", []).append(
            {"status": status.value, "timestamp": timestamp}
        )
    write_json(path, run)


def _lock_payload(edition: str, timestamp: str) -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "edition": edition,
        "created_at": timestamp,
    }


def _deadline_exceeded(run: dict[str, Any], timestamp: str | None = None) -> bool:
    deadline = run.get("deadline_at")
    if not deadline:
        return False
    now = datetime.fromisoformat(timestamp) if timestamp else datetime.now().astimezone()
    return now >= datetime.fromisoformat(str(deadline))


def _runtime_metrics(run: dict[str, Any], completed_at: str) -> dict[str, Any]:
    completed = datetime.fromisoformat(completed_at)
    started = datetime.fromisoformat(str(run.get("created_at", completed_at)))
    history = [
        entry
        for entry in run.get("stage_history", [])
        if isinstance(entry, dict) and entry.get("status") and entry.get("timestamp")
    ]
    if not history:
        history = [
            {"status": status, "timestamp": timestamp}
            for status, timestamp in run.get("stage_timestamps", {}).items()
        ]
    if not history or history[0].get("timestamp") != run.get("created_at"):
        history.insert(
            0,
            {
                "status": RunStatus.CREATED.value,
                "timestamp": str(run.get("created_at", completed_at)),
            },
        )
    history.append({"status": str(run.get("status", "completed")), "timestamp": completed_at})
    stage_durations: dict[str, int] = {}
    for current, following in zip(history, history[1:], strict=False):
        try:
            duration = max(
                0,
                int(
                    (
                        datetime.fromisoformat(str(following["timestamp"]))
                        - datetime.fromisoformat(str(current["timestamp"]))
                    ).total_seconds()
                ),
            )
        except (KeyError, TypeError, ValueError):
            continue
        status = str(current["status"])
        stage_durations[status] = stage_durations.get(status, 0) + duration
    grouped = {
        "collection": stage_durations.get(RunStatus.COLLECTING.value, 0),
        "context_building": stage_durations.get(RunStatus.BUILDING_CONTEXT.value, 0),
        "content_enrichment": stage_durations.get(RunStatus.EXTRACTING_CONTENT.value, 0),
        "agent_authoring_wait": (
            stage_durations.get(RunStatus.AWAITING_SELECTION.value, 0)
            + stage_durations.get(RunStatus.AWAITING_AUTHORING.value, 0)
        ),
        "validation_and_finalization": stage_durations.get(
            RunStatus.FINALIZING.value, 0
        ),
        "publication": stage_durations.get(RunStatus.PUBLISHING.value, 0),
    }
    authoring = run.get("artifacts", {}).get("authoring", {})
    authoring_metrics = (
        authoring.get("metrics") if isinstance(authoring, dict) else None
    )
    return {
        "elapsed_seconds": max(0, int((completed - started).total_seconds())),
        "deadline_exceeded": _deadline_exceeded(run, completed_at),
        "selected_fulltext_items": len(
            run.get("artifacts", {}).get("enrichment", {}).get("successful_item_ids", [])
        ),
        "attempt": int(run.get("attempt", 1)),
        "stage_durations_seconds": stage_durations,
        "phase_durations_seconds": grouped,
        "authoring": authoring_metrics,
    }


def _enrichment_evidence(index_path: Path, selected_ids: list[str]) -> dict[str, Any]:
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("Enriched index must be a JSON object")
    items = {
        str(item.get("item_id")): item
        for item in index.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    successful = [
        item_id
        for item_id in selected_ids
        if items.get(item_id, {}).get("content_status") in {"full_text", "partial"}
        and items.get(item_id, {}).get("content_path")
    ]
    full_text = [
        item_id
        for item_id in successful
        if items[item_id].get("content_status") == "full_text"
    ]
    partial = [item_id for item_id in successful if item_id not in set(full_text)]
    return {
        "successful_item_ids": successful,
        "full_text_item_ids": full_text,
        "partial_item_ids": partial,
        "unsuccessful_item_ids": [item_id for item_id in selected_ids if item_id not in successful],
    }


def _validate_enrichment_lineage(run: dict[str, Any], index_path: Path) -> None:
    enrichment = run.get("artifacts", {}).get("enrichment", {})
    if not isinstance(enrichment, dict):
        return
    expected = [str(item_id) for item_id in enrichment.get("successful_item_ids", [])]
    if not expected:
        return
    actual = _enrichment_evidence(index_path, expected)["successful_item_ids"]
    lost = sorted(set(expected) - set(actual))
    if lost:
        raise ValueError(
            "The final index lost previously successful full-text enrichment for item IDs: "
            f"{lost}. Finalize from the enriched run/index instead of restarting in another "
            "data directory."
        )


def adopt_index_for_run(config: AppConfig, data_dir: Path, index_path: Path) -> Path | None:
    index_path = require_data_root_path(index_path, data_dir, "Verified index")
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("Index must be a JSON object")
    run_path = data_dir / "runs" / str(index["date"]) / f"{index['edition']}.json"
    if not run_path.exists():
        return None
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    context_config = replace(
        config,
        output=replace(
            config.output,
            language=str(run.get("output_language") or "zh-CN"),
        ),
    )
    context_path = build_context(
        index_path,
        context_config,
        data_dir,
        str(index["edition"]),
        collection_window=run.get("collection_window"),
    )
    was_published = run.get("status") in {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_PARTIAL,
    }
    if was_published:
        previous = {
            key: value
            for key, value in run.get("artifacts", {}).items()
            if key
            in {
                "report_id",
                "json_path",
                "markdown_path",
                "content_hash",
                "notion",
            }
        }
        run["artifacts"] = {
            "index_path": str(index_path),
            "context_path": str(context_path),
            "previous_report": previous,
            "selected_item_ids": [],
        }
        run.pop("publication", None)
        run.pop("evaluation", None)
        run["revision_reason"] = "verified_source_supplement"
    else:
        run.setdefault("artifacts", {})["index_path"] = str(index_path)
        run["artifacts"]["context_path"] = str(context_path)
    _update_run(
        run_path,
        run,
        RunStatus.AWAITING_SELECTION,
        updated_at=now_iso(config.timezone),
        artifacts=run["artifacts"],
        next_action=(
            "Create and publish a report revision from the verified-source context."
            if was_published
            else "Select items from the refreshed verified-source context."
        ),
    )
    return run_path


def begin_authoring(run_path: Path, data_dir: Path) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") != RunStatus.AWAITING_AUTHORING:
        raise RuntimeError(
            f"Run must be awaiting authoring, got {run.get('status')!r}"
        )
    date = str(run["date"])
    edition = str(run["edition"])
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    with exclusive_lock(
        lock_path,
        _lock_payload(edition, now_iso(str(run.get("timezone", "Asia/Shanghai")))),
    ):
        context_path = require_data_root_path(
            Path(str(run["artifacts"]["context_path"])),
            data_dir,
            "Authoring context",
        )
        session_path = begin_authoring_session(run, context_path, data_dir)
        run["artifacts"]["authoring"] = {
            "session_path": str(session_path),
            "status": "dispatched",
        }
        _update_run(
            run_path,
            run,
            RunStatus.AWAITING_AUTHORING,
            artifacts=run["artifacts"],
            next_action=(
                "Dispatch every brief_authoring_batch once. Workers write only their "
                "assigned draft_result_path and submit it with submission_command."
            ),
        )
    return run_path


def accept_authoring_batch(
    run_path: Path,
    batch_id: str,
    result_path: Path,
    data_dir: Path,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") != RunStatus.AWAITING_AUTHORING:
        raise RuntimeError(
            f"Run must be awaiting authoring, got {run.get('status')!r}"
        )
    return submit_authoring_batch(run, batch_id, result_path, data_dir)


def accept_authoring_metrics(
    run_path: Path,
    metrics_path: Path,
    data_dir: Path,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") != RunStatus.AWAITING_AUTHORING:
        raise RuntimeError(
            f"Run must be awaiting authoring, got {run.get('status')!r}"
        )
    return record_authoring_metrics(run, metrics_path, data_dir)


def get_authoring_status(run_path: Path, data_dir: Path) -> dict[str, Any]:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    return inspect_authoring_status(run, data_dir)


def prefetch_authoring_media(
    run_path: Path,
    data_dir: Path,
    media_config: MediaConfig | None = None,
) -> dict[str, Any]:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    authoring = run.get("artifacts", {}).get("authoring", {})
    session_path = require_data_root_path(
        Path(str(authoring.get("session_path") or "")),
        data_dir,
        "Authoring session",
    )
    session = read_json(session_path)
    if not isinstance(session, dict):
        raise ValueError("Authoring session must be a JSON object")
    context_path = require_data_root_path(
        Path(str(session["context_path"])),
        data_dir,
        "Authoring context",
    )
    context = read_json(context_path)
    if not isinstance(context, dict):
        raise ValueError("Authoring context must be a JSON object")
    index_path = require_data_root_path(
        Path(str(run["artifacts"]["index_path"])),
        data_dir,
        "Run index",
    )
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("Run index must be a JSON object")
    item_ids = [
        str(item_id)
        for plan in context.get("brief_plan", [])
        if isinstance(plan, dict)
        for item_id in plan.get("default_item_ids", [])
    ]
    metrics = prefetch_index_images(
        index,
        item_ids,
        data_dir,
        media_config or MediaConfig(),
    )
    output_path = require_data_root_path(
        Path(str(session["paths"]["media_prefetch"])),
        data_dir,
        "Media prefetch receipt",
    )
    write_json(
        output_path,
        {
            "schema_version": "1.0",
            "completed_at": now_iso(str(run.get("timezone", "Asia/Shanghai"))),
            **metrics,
        },
    )
    return {"receipt_path": str(output_path), **metrics}


def prepare_authoring_analysis(
    run_path: Path,
    data_dir: Path,
    *,
    allow_degraded: bool = False,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") != RunStatus.AWAITING_AUTHORING:
        raise RuntimeError(
            f"Run must be awaiting authoring, got {run.get('status')!r}"
        )
    date = str(run["date"])
    edition = str(run["edition"])
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    with exclusive_lock(
        lock_path,
        _lock_payload(edition, now_iso(str(run.get("timezone", "Asia/Shanghai")))),
    ):
        prepared = prepare_analysis_packet(
            run,
            data_dir,
            allow_degraded=allow_degraded,
        )
        authoring = run["artifacts"]["authoring"]
        authoring.update(
            {
                "status": (
                    "degraded"
                    if prepared["missing_batches"]
                    else "analysis_pending"
                ),
                "analysis_packet_path": prepared["analysis_packet_path"],
                "analysis_result_path": prepared["analysis_result_path"],
                "brief_count": prepared["brief_count"],
                "batch_metrics": prepared["batch_metrics"],
                "missing_batches": prepared["missing_batches"],
                "coverage_targets": prepared["coverage_targets"],
            }
        )
        _update_run(
            run_path,
            run,
            RunStatus.AWAITING_AUTHORING,
            artifacts=run["artifacts"],
            budget_exhausted=bool(prepared["missing_batches"]),
            next_action=(
                "Read only analysis_packet_path, write the compact analysis payload to "
                "analysis_result_path, then run assemble-authoring."
            ),
        )
    return run_path


def assemble_authoring(
    run_path: Path,
    analysis_path: Path,
    data_dir: Path,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") != RunStatus.AWAITING_AUTHORING:
        raise RuntimeError(
            f"Run must be awaiting authoring, got {run.get('status')!r}"
        )
    date = str(run["date"])
    edition = str(run["edition"])
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    with exclusive_lock(
        lock_path,
        _lock_payload(edition, now_iso(str(run.get("timezone", "Asia/Shanghai")))),
    ):
        assembled = assemble_report_draft(run, analysis_path, data_dir)
        authoring = run["artifacts"]["authoring"]
        authoring.update(
            {
                "status": "ready",
                "report_draft_path": assembled["report_draft_path"],
                "metrics": assembled["metrics"],
                "coverage_targets": assembled["coverage_targets"],
            }
        )
        _update_run(
            run_path,
            run,
            RunStatus.AWAITING_AUTHORING,
            artifacts=run["artifacts"],
            next_action=(
                "Run validate-report with --run, then finalize-edition with the "
                "Python-assembled report_draft_path."
            ),
        )
    return run_path


def prepare_edition(
    config: AppConfig,
    data_dir: Path,
    edition: str,
    headed: bool = False,
    profile_dir: Path | None = None,
    browser_channel: str | None = None,
    restart: bool = False,
) -> Path:
    date = today_str(config.timezone)
    run_path = data_dir / "runs" / date / f"{edition}.json"
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    with exclusive_lock(
        lock_path,
        _lock_payload(edition, now_iso(config.timezone)),
    ):
        existing = read_json(run_path) if run_path.exists() else None
        if isinstance(existing, dict) and not restart:
            existing_language = str(existing.get("output_language") or "zh-CN")
            if existing_language != config.output.language:
                raise RuntimeError(
                    f"Existing {edition} run uses output language "
                    f"{existing_language!r}; rerun with --restart to create a "
                    f"{config.output.language!r} revision"
                )
            if existing.get("status") not in {status.value for status in TERMINAL_STATUSES}:
                return run_path
            if existing.get("status") in {
                RunStatus.COMPLETED,
                RunStatus.COMPLETED_PARTIAL,
            }:
                return run_path

        attempt = int(existing.get("attempt", 0)) + 1 if isinstance(existing, dict) else 1
        created_at = now_iso(config.timezone)
        run: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": f"run-{date}-{edition}",
            "date": date,
            "edition": edition,
            "output_language": config.output.language,
            "timezone": config.timezone,
            "data_root": str(data_dir.resolve()),
            "attempt": attempt,
            "status": RunStatus.CREATED,
            "created_at": created_at,
            "updated_at": created_at,
            "stage_timestamps": {RunStatus.CREATED.value: created_at},
            "stage_history": [
                {"status": RunStatus.CREATED.value, "timestamp": created_at}
            ],
            "collection_window": edition_window(date, edition, config.timezone),
            "budget": {
                "max_runtime_seconds": config.budget.max_runtime_seconds,
                "max_agent_tokens": config.budget.max_agent_tokens,
                "max_fulltext_per_run": config.budget.max_fulltext_per_run,
                "report_items_per_source": config.budget.report_items_per_source,
            },
            "deadline_at": (
                datetime.now(ZoneInfo(config.timezone))
                + timedelta(seconds=config.budget.max_runtime_seconds)
            ).isoformat(timespec="seconds"),
            "artifacts": {},
            "pending_sources": [],
            "error": None,
        }
        write_json(run_path, run)
        try:
            feedback_path = None
            feedback_warning = None
            try:
                feedback_path = sync_user_feedback(data_dir)
            except Exception as exc:
                feedback_warning = f"{type(exc).__name__}: {exc}"
            _update_run(
                run_path,
                run,
                RunStatus.COLLECTING,
                updated_at=now_iso(config.timezone),
            )
            monitor_path = None
            monitor_warning = None
            monitor_refreshed = False
            monitor_reused = False
            if config.monitor.enabled:
                if config.monitor.reuse_fresh_snapshot_before_edition:
                    monitor_path = fresh_monitor_snapshot_path(
                        data_dir,
                        config.timezone,
                        config.monitor.snapshot_max_age_minutes,
                    )
                    monitor_reused = monitor_path is not None
                if config.monitor.refresh_before_edition and monitor_path is None:
                    try:
                        monitor_path = refresh_monitor(config, data_dir)
                        monitor_refreshed = True
                    except Exception as exc:
                        monitor_warning = f"{type(exc).__name__}: {exc}"
            index_path = collect_sources(
                config=config,
                data_dir=data_dir,
                edition=edition,
                headed=headed,
                profile_dir=profile_dir,
                browser_channel=browser_channel,
            )
            index = read_json(index_path)
            source_rows = [
                row for row in index.get("sources", []) if isinstance(row, dict)
            ]
            status_breakdown: dict[str, int] = {}
            for row in source_rows:
                status = str(row.get("status", "unknown"))
                status_breakdown[status] = status_breakdown.get(status, 0) + 1
            collection_metrics = {
                "source_count": len(source_rows),
                "candidate_count": len(index.get("items", [])),
                "status_breakdown": status_breakdown,
                "http_prefetch": True,
                "collection_global_concurrency": (
                    config.browser.collection_global_concurrency
                ),
                    "collection_per_domain_concurrency": (
                        config.browser.collection_per_domain_concurrency
                    ),
                    "monitor_refresh": monitor_refreshed,
                    "monitor_snapshot_reused": monitor_reused,
                    "monitor_token_usage": 0,
                }
            pending = [
                row["source_id"]
                for row in index.get("sources", [])
                if row.get("status")
                in {"failed", "verification_required", "rate_limited", "partial"}
            ]
            verification_sources = [
                row["source_id"]
                for row in index.get("sources", [])
                if row.get("status") in {"verification_required", "rate_limited"}
            ]
            _update_run(
                run_path,
                run,
                RunStatus.BUILDING_CONTEXT,
                updated_at=now_iso(config.timezone),
                artifacts={
                    "index_path": str(index_path),
                    "collection_metrics": collection_metrics,
                    **(
                        {"monitor_snapshot_path": str(monitor_path)}
                        if monitor_path
                        else {}
                    ),
                    **({"user_feedback_path": str(feedback_path)} if feedback_path else {}),
                },
                pending_sources=pending,
                verification_sources=verification_sources,
                feedback_sync_warning=feedback_warning,
                monitor_refresh_warning=monitor_warning,
            )
            context_path = build_context(
                index_path,
                config,
                data_dir,
                edition,
                collection_window=run["collection_window"],
            )
            run["artifacts"]["context_path"] = str(context_path)
            _update_run(
                run_path,
                run,
                RunStatus.AWAITING_SELECTION,
                updated_at=now_iso(config.timezone),
                artifacts=run["artifacts"],
                next_action=(
                    "Select item IDs, run enrich-edition, then author from the refreshed context. "
                    "In an interactive desktop session, run verify-pending for challenged sources "
                    "before resume; unattended runs continue without GUI verification."
                ),
            )
            return run_path
        except Exception as exc:
            _update_run(
                run_path,
                run,
                RunStatus.FAILED,
                updated_at=now_iso(config.timezone),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise


def enrich_edition(
    run_path: Path,
    config: AppConfig,
    data_dir: Path,
    selected_ids: list[str],
    max_items: int | None,
    headed: bool = False,
    profile_dir: Path | None = None,
    browser_channel: str | None = None,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") not in {
        RunStatus.AWAITING_SELECTION,
        RunStatus.AWAITING_AUTHORING,
    }:
        raise RuntimeError(
            f"Run must be awaiting selection or authoring, got {run.get('status')!r}"
        )

    date = str(run["date"])
    edition = str(run["edition"])
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    with exclusive_lock(
        lock_path,
        _lock_payload(edition, now_iso(config.timezone)),
    ):
        index_path = require_data_root_path(
            Path(run["artifacts"]["index_path"]), data_dir, "Run index"
        )
        previous_ids = list(run.get("artifacts", {}).get("selected_item_ids", []))
        requested_ids = [
            item_id
            for item_id in dict.fromkeys(selected_ids)
            if item_id not in previous_ids
        ]
        remaining_budget = max(
            0, config.budget.max_fulltext_per_run - len(previous_ids)
        )
        requested_limit = (
            config.budget.max_fulltext_per_run if max_items is None else max_items
        )
        if requested_limit < 0:
            raise ValueError("max_items cannot be negative")
        accepted_ids = requested_ids[: min(requested_limit, remaining_budget)]
        if _deadline_exceeded(run):
            accepted_ids = []
            run["budget_exhausted"] = True
        cumulative_ids = [*previous_ids, *accepted_ids]
        if accepted_ids:
            _update_run(
                run_path,
                run,
                RunStatus.EXTRACTING_CONTENT,
                updated_at=now_iso(config.timezone),
            )
            index_path = extract_content(
                index_path=index_path,
                config=config,
                data_dir=data_dir,
                selected_ids=accepted_ids,
                max_items=len(accepted_ids),
                headed=headed,
                profile_dir=profile_dir,
                browser_channel=browser_channel,
            )
        context_config = replace(
            config,
            output=replace(
                config.output,
                language=str(
                    run.get("output_language") or config.output.language
                ),
            ),
        )
        context_path = build_context(
            index_path,
            context_config,
            data_dir,
            edition,
            collection_window=run["collection_window"],
        )
        evidence = _enrichment_evidence(index_path, cumulative_ids)
        enriched_index = read_json(index_path)
        content_metrics = (
            enriched_index.get("content_metrics", {})
            if isinstance(enriched_index, dict)
            and isinstance(enriched_index.get("content_metrics"), dict)
            else {}
        )
        enrichment = {
            "requested": len(requested_ids),
            "accepted": len(accepted_ids),
            "hard_cap": config.budget.max_fulltext_per_run,
            "global_concurrency": config.browser.global_concurrency,
            "per_domain_concurrency": config.browser.per_domain_concurrency,
            **evidence,
        }
        if content_metrics:
            enrichment["pipeline"] = content_metrics
        run["artifacts"].update(
            {
                "index_path": str(index_path),
                "context_path": str(context_path),
                "selected_item_ids": cumulative_ids,
                "enrichment": enrichment,
            }
        )
        _update_run(
            run_path,
            run,
            RunStatus.AWAITING_AUTHORING,
            updated_at=now_iso(config.timezone),
            artifacts=run["artifacts"],
            next_action=(
                "Author a report from context_path, then run finalize-edition "
                "with this run manifest and the report draft."
            ),
        )
        return run_path


def _coverage_targets_for_run(
    run: dict[str, Any],
    data_dir: Path,
) -> dict[str, int] | None:
    authoring = run.get("artifacts", {}).get("authoring", {})
    values = authoring.get("coverage_targets")
    if values is None and authoring.get("session_path"):
        session_path = require_data_root_path(
            Path(str(authoring["session_path"])),
            data_dir,
            "Authoring session",
        )
        session = read_json(session_path)
        if isinstance(session, dict):
            values = session.get("coverage_targets")
    if not isinstance(values, dict):
        return None
    return {str(key): value for key, value in values.items()}


def _require_current_context_schema(
    draft: dict[str, Any],
    run: dict[str, Any],
    data_dir: Path,
) -> None:
    context_value = run.get("artifacts", {}).get("context_path")
    if not context_value:
        return
    context_path = require_data_root_path(
        Path(str(context_value)),
        data_dir,
        "Authoring context",
    )
    context = read_json(context_path)
    if (
        isinstance(context, dict)
        and context.get("schema_version") == "2.0"
        and draft.get("schema_version") != "2.0"
    ):
        raise ValueError(
            "Current authoring context requires report schema_version '2.0'; "
            "legacy 1.5 drafts cannot bypass cross_perspective_synthesis"
        )


def finalize_edition(
    run_path: Path,
    report_path: Path,
    data_dir: Path,
    publish: bool = False,
    force_publish: bool = False,
    notion_config: Path | None = None,
    output_config: OutputConfig | None = None,
    media_config: MediaConfig | None = None,
    defer_tail: bool = False,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    date = str(run["date"])
    edition = str(run["edition"])
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    timezone = str(run.get("timezone", "Asia/Shanghai"))
    lock_timestamp = now_iso(timezone)
    with exclusive_lock(lock_path, _lock_payload(edition, lock_timestamp)):
        if run.get("status") in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_PARTIAL,
        }:
            tail = run.get("tail", {})
            if isinstance(tail, dict) and tail.get("status") in {
                "pending",
                "running",
                "partial",
            }:
                return run_path
            evaluation = run.get("evaluation", {})
            scheduler = evaluation.get("scheduler", {}) if isinstance(evaluation, dict) else {}
            if (
                isinstance(evaluation, dict)
                and evaluation.get("status") != "completed"
                and scheduler.get("status") != "scheduled"
            ):
                evaluation["scheduler"] = schedule_independent_evaluation(
                    Path(run["artifacts"]["json_path"]),
                    Path(run["artifacts"]["index_path"]),
                    data_dir,
                    str(run["artifacts"]["report_id"]),
                    str(run["artifacts"]["content_hash"]),
                    publish_notion=bool(run.get("publication")),
                )
                _update_run(
                    run_path,
                    run,
                    RunStatus(run["status"]),
                    evaluation=evaluation,
                    next_action=(
                        "Independent evaluation is pending and will run asynchronously."
                        if evaluation["scheduler"]["status"] == "scheduled"
                        else "Automatic evaluator scheduling failed; retry finalize-edition."
                    ),
                )
            return run_path
        recoverable = {
            RunStatus.AWAITING_AUTHORING,
            RunStatus.FINALIZING,
            RunStatus.PUBLISHING,
        }
        if run.get("status") not in recoverable:
            raise RuntimeError(f"Run is not ready to finalize, got {run.get('status')!r}")

        draft = read_json(report_path)
        if not isinstance(draft, dict):
            raise ValueError("Report draft must be a JSON object")
        draft.setdefault("date", date)
        draft.setdefault("edition", edition)
        if draft.get("date") != date or draft.get("edition") != edition:
            raise ValueError("Report draft date and edition must match the run manifest")
        _require_current_context_schema(draft, run, data_dir)

        artifacts = run["artifacts"]
        index_path = require_data_root_path(
            Path(artifacts["index_path"]), data_dir, "Final report index"
        )
        _validate_enrichment_lineage(run, index_path)
        if not artifacts.get("json_path"):
            _update_run(run_path, run, RunStatus.FINALIZING)
            try:
                requested_output = output_config or OutputConfig()
                if defer_tail and "html" not in requested_output.formats:
                    raise ValueError(
                        "--defer-tail requires HTML in output.formats because HTML "
                        "is the foreground deliverable"
                    )
                local_output = (
                    replace(
                        requested_output,
                        formats=[
                            value
                            for value in requested_output.formats
                            if value != "pdf"
                        ],
                    )
                    if defer_tail
                    else requested_output
                )
                saved = save_report(
                    report_path,
                    index_path,
                    data_dir,
                    output_config=local_output,
                    media_config=media_config,
                    coverage_targets=_coverage_targets_for_run(run, data_dir),
                )
            except Exception as exc:
                _update_run(
                    run_path,
                    run,
                    RunStatus.AWAITING_AUTHORING,
                    updated_at=now_iso(timezone),
                    error=f"{type(exc).__name__}: {exc}",
                    next_action="Fix the report draft and retry finalize-edition.",
                )
                raise
            run["artifacts"].update(saved)
            artifacts = run["artifacts"]
            milestones = dict(run.get("milestones", {}))
            milestones["local_html_ready_at"] = now_iso(timezone)
            saved_report = read_json(Path(artifacts["json_path"]))
            if isinstance(saved_report, dict):
                successful = artifacts.get("enrichment", {}).get(
                    "successful_item_ids", []
                )
                artifacts["evidence_metrics"] = {
                    "featured_events": int(saved_report.get("event_count", 0)),
                    "successful_fulltext_items": len(successful),
                    "analysis_without_fulltext": bool(
                        saved_report.get("analyses") and not successful
                    ),
                }
            _update_run(
                run_path,
                run,
                RunStatus.FINALIZING,
                artifacts=artifacts,
                milestones=milestones,
            )

        if defer_tail:
            local_ready_at = str(
                run.get("milestones", {}).get("local_html_ready_at")
                or now_iso(timezone)
            )
            requested_output = output_config or OutputConfig()
            final_status = (
                RunStatus.COMPLETED_PARTIAL
                if run.get("pending_sources") or run.get("budget_exhausted")
                else RunStatus.COMPLETED
            )
            tail_command = (
                f'daily-intel --data-dir "{data_dir}" complete-edition-tail '
                f'--run "{run_path}"'
                + (" --publish" if publish else "")
                + (
                    f' --notion-config "{notion_config}"'
                    if notion_config is not None
                    else ""
                )
            )
            evaluation_state = {
                "status": "pending",
                "report_id": run["artifacts"].get("report_id"),
                "content_hash": run["artifacts"].get("content_hash"),
                "scheduler": {"status": "deferred_until_tail"},
                "next_action": (
                    "The isolated evaluator will be scheduled by complete-edition-tail."
                ),
            }
            tail = {
                "status": "pending",
                "requested_at": local_ready_at,
                "requested_formats": list(requested_output.formats),
                "publish_requested": publish,
                "notion_config": str(notion_config) if notion_config is not None else None,
                "command": tail_command,
                "next_action": (
                    "Run command in a background terminal with completion notification."
                ),
            }
            milestones = dict(run.get("milestones", {}))
            milestones["local_html_ready_at"] = local_ready_at
            _update_run(
                run_path,
                run,
                final_status,
                updated_at=local_ready_at,
                artifacts=run["artifacts"],
                publication=None,
                evaluation=evaluation_state,
                tail=tail,
                milestones=milestones,
                metrics=_runtime_metrics(run, local_ready_at),
                error=None,
                next_action=tail["next_action"],
            )
            return run_path

        publication = run.get("publication")
        if publish and not publication:
            _update_run(run_path, run, RunStatus.PUBLISHING)
            try:
                page_id, publication_status = publish_report(
                    Path(artifacts["json_path"]),
                    data_dir,
                    force=force_publish,
                    config_path=notion_config,
                )
            except Exception as exc:
                _update_run(
                    run_path,
                    run,
                    RunStatus.PUBLISHING,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            publication = {
                "page_id": page_id,
                "status": publication_status,
            }
            run["artifacts"]["notion"] = publication
            run.setdefault("milestones", {})["notion_ready_at"] = now_iso(timezone)

        final_status = (
            RunStatus.COMPLETED_PARTIAL
            if run.get("pending_sources") or run.get("budget_exhausted")
            else RunStatus.COMPLETED
        )
        evaluation_state: dict[str, Any] = {
            "status": "pending",
            "report_id": run["artifacts"].get("report_id"),
            "content_hash": run["artifacts"].get("content_hash"),
            "next_action": (
                "Wait for the isolated post-publication evaluator; evaluation advice never "
                "blocks this report."
            ),
        }
        completed_at = now_iso(timezone)
        _update_run(
            run_path,
            run,
            final_status,
            updated_at=completed_at,
            artifacts=run["artifacts"],
            publication=publication,
            evaluation=evaluation_state,
            milestones=run.get("milestones", {}),
            metrics=_runtime_metrics(run, completed_at),
            error=None,
            next_action="Independent evaluation is pending and will run asynchronously.",
        )
        evaluation_state["scheduler"] = schedule_independent_evaluation(
            Path(run["artifacts"]["json_path"]),
            Path(run["artifacts"]["index_path"]),
            data_dir,
            str(run["artifacts"]["report_id"]),
            str(run["artifacts"]["content_hash"]),
            publish_notion=bool(publication),
        )
        if evaluation_state["scheduler"]["status"] != "scheduled":
            evaluation_state["next_action"] = (
                "Automatic evaluator scheduling failed; keep the local report and retry "
                "evaluation separately."
            )
        _update_run(
            run_path,
            run,
            final_status,
            updated_at=now_iso(timezone),
            evaluation=evaluation_state,
            next_action=evaluation_state["next_action"],
        )
        return run_path


def complete_edition_tail(
    run_path: Path,
    data_dir: Path,
    *,
    publish: bool = False,
    notion_config: Path | None = None,
    output_config: OutputConfig | None = None,
) -> Path:
    run_path = require_data_root_path(run_path, data_dir, "Run manifest")
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    validate_run_data_root(run, run_path, data_dir)
    if run.get("status") not in {
        RunStatus.COMPLETED,
        RunStatus.COMPLETED_PARTIAL,
    }:
        raise RuntimeError(
            f"Tail work requires a locally completed run, got {run.get('status')!r}"
        )
    date = str(run["date"])
    edition = str(run["edition"])
    timezone = str(run.get("timezone", "Asia/Shanghai"))
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    with exclusive_lock(lock_path, _lock_payload(edition, now_iso(timezone))):
        run = read_json(run_path)
        if not isinstance(run, dict):
            raise ValueError("Run manifest must be a JSON object")
        tail = dict(run.get("tail") or {})
        if tail.get("status") == "completed":
            return run_path
        artifacts = run["artifacts"]
        report_path = require_data_root_path(
            Path(str(artifacts["json_path"])),
            data_dir,
            "Saved report",
        )
        report = read_json(report_path)
        if not isinstance(report, dict):
            raise ValueError("Saved report must be a JSON object")
        tail_started_at = now_iso(timezone)
        tail.update({"status": "running", "started_at": tail_started_at})
        _update_run(
            run_path,
            run,
            RunStatus(run["status"]),
            tail=tail,
            next_action="PDF/Notion tail work is running in the background.",
        )

        errors: list[str] = []
        warnings: list[str] = []

        requested_formats = [
            str(value) for value in tail.get("requested_formats", ["html", "pdf"])
        ]
        requested_output = output_config or OutputConfig()
        tail_output = replace(
            requested_output,
            formats=requested_formats,
            open_after_finalize=False,
        )
        local_projection_seconds = None
        if "pdf" in requested_formats and not artifacts.get("pdf_path"):
            projection_started = perf_counter()
            try:
                local_outputs = write_local_outputs(
                    report,
                    data_dir,
                    tail_output,
                    open_after_finalize=False,
                )
                warnings.extend(local_outputs.pop("warnings", []))
                artifacts.update(local_outputs)
                if local_outputs.get("pdf_error"):
                    errors.append(str(local_outputs["pdf_error"]))
                elif local_outputs.get("pdf_path"):
                    run.setdefault("milestones", {})["pdf_ready_at"] = now_iso(
                        timezone
                    )
            except Exception as exc:
                errors.append(
                    f"Local PDF projection failed: {type(exc).__name__}: {exc}"
                )
            finally:
                local_projection_seconds = round(
                    max(0.0, perf_counter() - projection_started),
                    3,
                )

        publication = run.get("publication")
        publish_requested = bool(publish or tail.get("publish_requested"))
        configured_notion_path = notion_config
        if configured_notion_path is None and tail.get("notion_config"):
            configured_notion_path = Path(str(tail["notion_config"]))
        if publish_requested and not publication:
            try:
                page_id, publication_status = publish_report(
                    report_path,
                    data_dir,
                    config_path=configured_notion_path,
                )
                publication = {
                    "page_id": page_id,
                    "status": publication_status,
                }
                artifacts["notion"] = publication
                run.setdefault("milestones", {})["notion_ready_at"] = now_iso(timezone)
            except Exception as exc:
                errors.append(f"Notion publish failed: {type(exc).__name__}: {exc}")

        evaluation = run.get("evaluation")
        if not isinstance(evaluation, dict):
            evaluation = {
                "status": "pending",
                "report_id": artifacts.get("report_id"),
                "content_hash": artifacts.get("content_hash"),
            }
        scheduler = evaluation.get("scheduler")
        if (
            evaluation.get("status") != "completed"
            and (
                not isinstance(scheduler, dict)
                or scheduler.get("status") not in {"scheduled", "completed"}
            )
        ):
            evaluation["scheduler"] = schedule_independent_evaluation(
                report_path,
                Path(artifacts["index_path"]),
                data_dir,
                str(artifacts["report_id"]),
                str(artifacts["content_hash"]),
                publish_notion=bool(publication),
            )
            if evaluation["scheduler"].get("status") != "scheduled":
                errors.append(
                    "Independent evaluator scheduling failed: "
                    + str(evaluation["scheduler"].get("error") or "unknown error")
                )
        evaluation["next_action"] = (
            "Independent evaluation is pending and will run asynchronously."
        )

        completed_at = now_iso(timezone)
        started = datetime.fromisoformat(tail_started_at)
        completed = datetime.fromisoformat(completed_at)
        tail.update(
            {
                "status": "partial" if errors else "completed",
                "completed_at": completed_at,
                "duration_seconds": round(
                    max(0.0, (completed - started).total_seconds()),
                    3,
                ),
                "warnings": warnings,
                "errors": errors,
                "next_action": (
                    "Retry complete-edition-tail; local HTML remains authoritative."
                    if errors
                    else "Wait for the isolated evaluator."
                ),
            }
        )
        metrics = dict(run.get("metrics", {}))
        metrics["tail_seconds"] = tail["duration_seconds"]
        if local_projection_seconds is not None:
            metrics["pdf_projection_seconds"] = local_projection_seconds
        _update_run(
            run_path,
            run,
            RunStatus(run["status"]),
            updated_at=completed_at,
            artifacts=artifacts,
            publication=publication,
            evaluation=evaluation,
            tail=tail,
            milestones=run.get("milestones", {}),
            metrics=metrics,
            error="; ".join(errors) if errors else None,
            next_action=tail["next_action"],
        )
        return run_path
