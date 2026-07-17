from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .collector import collect_sources
from .config import AppConfig, project_root
from .content import extract_content
from .context import build_context
from .notion import publish_report, sync_user_feedback
from .reports import save_report
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
) -> dict[str, Any]:
    """Create a bounded retrying Hermes job; scheduling failure never rolls back publication."""
    draft_path = data_dir / "evaluations" / "drafts" / f"{report_id}.json"
    prompt = (
        "你是发布后独立评估 Agent，不参与日报生成，也不得修改主报告。"
        f"只读不可变报告 {report_path}、索引 {index_path} 和技能中的 "
        "templates/report-contract.md；按九个固定维度各给 1—5 分，简洁指出主要缺陷、"
        "证据不足和改进建议。"
        f"被评报告 ID 是 {report_id}，内容 SHA-256 是 {content_hash}。"
        f"把评估 JSON 写到 {draft_path}，然后执行：daily-intel --data-dir \"{data_dir}\" "
        f"finalize-evaluation --report \"{report_path}\" --evaluation \"{draft_path}\" "
        "--publish。若该 report_id 已有 completed 评估则直接退出；不得要求用户点击。"
        "该任务最多由调度器尝试三次，以容忍临时模型/API 连接失败。"
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
    run.update(fields)
    run["status"] = status
    run["updated_at"] = timestamp
    run.setdefault("stage_timestamps", {})[status.value] = timestamp
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
    return {
        "elapsed_seconds": max(0, int((completed - started).total_seconds())),
        "deadline_exceeded": _deadline_exceeded(run, completed_at),
        "selected_fulltext_items": len(run.get("artifacts", {}).get("selected_item_ids", [])),
        "attempt": int(run.get("attempt", 1)),
    }


def adopt_index_for_run(config: AppConfig, data_dir: Path, index_path: Path) -> Path | None:
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("Index must be a JSON object")
    run_path = data_dir / "runs" / str(index["date"]) / f"{index['edition']}.json"
    if not run_path.exists():
        return None
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    context_path = build_context(
        index_path,
        config,
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
            if existing.get("status") not in {status.value for status in TERMINAL_STATUSES}:
                return run_path
            if existing.get("status") in {
                RunStatus.COMPLETED,
                RunStatus.COMPLETED_PARTIAL,
            }:
                return run_path

        attempt = int(existing.get("attempt", 0)) + 1 if isinstance(existing, dict) else 1
        run: dict[str, Any] = {
            "schema_version": "1.0",
            "run_id": f"run-{date}-{edition}",
            "date": date,
            "edition": edition,
            "timezone": config.timezone,
            "attempt": attempt,
            "status": RunStatus.CREATED,
            "created_at": now_iso(config.timezone),
            "updated_at": now_iso(config.timezone),
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
            index_path = collect_sources(
                config=config,
                data_dir=data_dir,
                edition=edition,
                headed=headed,
                profile_dir=profile_dir,
                browser_channel=browser_channel,
            )
            index = read_json(index_path)
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
                    **({"user_feedback_path": str(feedback_path)} if feedback_path else {}),
                },
                pending_sources=pending,
                verification_sources=verification_sources,
                feedback_sync_warning=feedback_warning,
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
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
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
        index_path = Path(run["artifacts"]["index_path"])
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
        context_path = build_context(
            index_path,
            config,
            data_dir,
            edition,
            collection_window=run["collection_window"],
        )
        run["artifacts"].update(
            {
                "index_path": str(index_path),
                "context_path": str(context_path),
                "selected_item_ids": cumulative_ids,
                "enrichment": {
                    "requested": len(requested_ids),
                    "accepted": len(accepted_ids),
                    "hard_cap": config.budget.max_fulltext_per_run,
                    "global_concurrency": config.browser.global_concurrency,
                    "per_domain_concurrency": config.browser.per_domain_concurrency,
                },
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


def finalize_edition(
    run_path: Path,
    report_path: Path,
    data_dir: Path,
    publish: bool = False,
    force_publish: bool = False,
    notion_config: Path | None = None,
) -> Path:
    run = read_json(run_path)
    if not isinstance(run, dict):
        raise ValueError("Run manifest must be a JSON object")
    date = str(run["date"])
    edition = str(run["edition"])
    lock_path = data_dir / "locks" / f"{date}-{edition}.lock"
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with exclusive_lock(lock_path, _lock_payload(edition, timestamp)):
        if run.get("status") in {
            RunStatus.COMPLETED,
            RunStatus.COMPLETED_PARTIAL,
        }:
            evaluation = run.get("evaluation", {})
            scheduler = evaluation.get("scheduler", {}) if isinstance(evaluation, dict) else {}
            if (
                publish
                and run.get("publication")
                and isinstance(evaluation, dict)
                and evaluation.get("status") != "completed"
                and scheduler.get("status") != "scheduled"
            ):
                evaluation["scheduler"] = schedule_independent_evaluation(
                    Path(run["artifacts"]["json_path"]),
                    Path(run["artifacts"]["index_path"]),
                    data_dir,
                    str(run["artifacts"]["report_id"]),
                    str(run["artifacts"]["content_hash"]),
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

        artifacts = run["artifacts"]
        if not artifacts.get("json_path"):
            _update_run(run_path, run, RunStatus.FINALIZING)
            try:
                saved = save_report(
                    report_path,
                    Path(run["artifacts"]["index_path"]),
                    data_dir,
                )
            except Exception as exc:
                _update_run(
                    run_path,
                    run,
                    RunStatus.AWAITING_AUTHORING,
                    updated_at=timestamp,
                    error=f"{type(exc).__name__}: {exc}",
                    next_action="Fix the report draft and retry finalize-edition.",
                )
                raise
            run["artifacts"].update(saved)
            artifacts = run["artifacts"]
            _update_run(
                run_path,
                run,
                RunStatus.FINALIZING,
                artifacts=artifacts,
            )

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

        final_status = (
            RunStatus.COMPLETED_PARTIAL if run.get("pending_sources") else RunStatus.COMPLETED
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
        _update_run(
            run_path,
            run,
            final_status,
            updated_at=timestamp,
            artifacts=run["artifacts"],
            publication=publication,
            evaluation=evaluation_state,
            metrics=_runtime_metrics(run, timestamp),
            error=None,
            next_action="Independent evaluation is pending and will run asynchronously.",
        )
        if publish and publication:
            evaluation_state["scheduler"] = schedule_independent_evaluation(
                Path(run["artifacts"]["json_path"]),
                Path(run["artifacts"]["index_path"]),
                data_dir,
                str(run["artifacts"]["report_id"]),
                str(run["artifacts"]["content_hash"]),
            )
            if evaluation_state["scheduler"]["status"] != "scheduled":
                evaluation_state["next_action"] = (
                    "Automatic evaluator scheduling failed; keep the published report and retry "
                    "evaluation separately."
                )
            _update_run(
                run_path,
                run,
                final_status,
                updated_at=now_iso(str(run.get("timezone", "Asia/Shanghai"))),
                evaluation=evaluation_state,
                next_action=evaluation_state["next_action"],
            )
        return run_path
