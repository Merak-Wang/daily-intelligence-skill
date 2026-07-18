from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import AppConfig
from .reporting import evaluation_continuity_floor
from .semantics import load_semantic_cache, reusable_semantic_brief, semantic_fingerprint
from .storage import next_revision, write_immutable_json
from .utils import now_iso, read_json, write_json

_CANDIDATE_FIELDS = (
    "item_id",
    "source_id",
    "source_name",
    "title",
    "url",
    "description",
    "published_at",
    "discovered_at",
    "module",
    "category",
    "content_status",
    "content_path",
    "image_url",
)


def _read_state(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        payload = {"schema_version": "1.0", "items": []}
        write_json(path, payload)
        return []
    raw = read_json(path)
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return [item for item in raw["items"] if isinstance(item, dict)]
    raise ValueError(f"Invalid state file: {path}")


def _load_reports(
    reports_dir: Path,
    date: str,
    edition: str,
) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    reports: list[tuple[str, Path, dict[str, Any]]] = []
    warnings: list[str] = []
    for path in reports_dir.glob("*/*.json"):
        try:
            report = read_json(path)
            if isinstance(report, dict):
                reports.append((str(report.get("generated_at", "")), path, report))
        except Exception as exc:
            warnings.append(f"Skipped unreadable report {path}: {type(exc).__name__}: {exc}")
    reports.sort(key=lambda item: item[0], reverse=True)

    selected: list[tuple[str, Path, dict[str, Any]]] = []
    if edition == "evening":
        selected.extend(
            item
            for item in reports
            if item[2].get("date") == date and item[2].get("edition") == "morning"
        )
    else:
        selected.extend(
            item
            for item in reports
            if item[2].get("date") < date and item[2].get("edition") == "evening"
        )
    selected_ids = {item[2].get("report_id") for item in selected}
    selected.extend(item for item in reports if item[2].get("report_id") not in selected_ids)
    recent = selected[:5]
    entries = [_continuity_entry(path, report) for _generated_at, path, report in recent]
    reported_item_ids = {
        str(brief["item_id"])
        for _generated_at, _path, report in recent
        for section in report.get("sections", [])
        for brief in section.get("briefs", [])
        if isinstance(brief, dict) and brief.get("item_id")
    }
    return entries, warnings, reported_item_ids


def _separate_evaluation(path: Path, report: dict[str, Any]) -> dict[str, Any] | None:
    evaluation_dir = path.parents[2] / "evaluations" / str(report.get("date", ""))
    candidates: list[tuple[int, dict[str, Any]]] = []
    for evaluation_path in evaluation_dir.glob(f"{report.get('edition')}-r*.json"):
        try:
            value = read_json(evaluation_path)
        except Exception:
            continue
        if isinstance(value, dict) and value.get("evaluated_report_id") == report.get("report_id"):
            try:
                revision = int(evaluation_path.stem.rsplit("-r", 1)[1])
            except (IndexError, ValueError):
                revision = 0
            candidates.append((revision, value))
    return max(candidates, default=(0, None), key=lambda item: item[0])[1]


def _continuity_entry(path: Path, report: dict[str, Any]) -> dict[str, Any]:
    evaluation = _separate_evaluation(path, report) or report.get("quality_evaluation")
    if isinstance(evaluation, dict):
        decision, excluded, continuity_override = evaluation_continuity_floor(evaluation)
    else:
        decision = "selective"
        excluded = {"formatting", "event_summaries", "analyses"}
        continuity_override = None
    reject = decision == "reject" or "all" in excluded
    events = []
    analyses = []
    if not reject:
        for section in report.get("sections", []):
            for item in section.get("items", []):
                event = {
                    "event_id": item.get("event_id"),
                    "status": item.get("status"),
                    "source_item_ids": [
                        ref.get("item_id") for ref in item.get("source_refs", [])
                    ],
                }
                if "event_summaries" not in excluded:
                    event.update(
                        {
                            "title": item.get("title"),
                            "importance": item.get("importance"),
                        }
                    )
                events.append(event)
    if not reject and "analyses" not in excluded:
        analyses = [
            {
                key: analysis.get(key)
                for key in (
                    "analysis_id",
                    "claim",
                    "confidence",
                    "state_change",
                    "evidence_event_ids",
                    "counter_evidence",
                    "watch_signals",
                )
            }
            for analysis in report.get("analyses", [])
        ]
    return {
        "path": str(path),
        "report_id": report.get("report_id"),
        "date": report.get("date"),
        "edition": report.get("edition"),
        "reuse_status": decision,
        "excluded": sorted(excluded),
        "quality_evaluation": evaluation,
        "continuity_override": continuity_override,
        "events": events,
        "analyses": analyses,
    }


def _compact_candidates(
    index: dict[str, Any],
    per_source: int,
    report_targets: dict[str, int],
    reported_item_ids: set[str],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in index.get("items", []):
        if isinstance(item, dict) and item.get("source_id"):
            grouped.setdefault(str(item["source_id"]), []).append(item)
    compact: list[dict[str, Any]] = []
    for source_id, source_items in grouped.items():
        ranked_items: list[tuple[dict[str, Any], int]] = []
        for fallback_rank, item in enumerate(source_items, start=1):
            metadata = item.get("metadata", {})
            ranked_items.append(
                (
                    item,
                    int(
                        metadata.get("source_rank")
                        or metadata.get("hot_rank")
                        or metadata.get("list_position")
                        or fallback_rank
                    ),
                )
            )

        def sort_key(row: tuple[dict[str, Any], int]) -> tuple[int, int, float, int]:
            item, source_rank = row
            enriched = item.get("content_status") in {"full_text", "partial"}
            published_at = str(item.get("published_at") or "").strip()
            published_timestamp: float | None = None
            if published_at:
                try:
                    parsed = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=UTC)
                    published_timestamp = parsed.timestamp()
                except ValueError:
                    published_timestamp = None
            return (
                0 if enriched else 1,
                0 if published_timestamp is not None else 1,
                -(published_timestamp or 0.0),
                source_rank,
            )

        ranked_items.sort(key=sort_key)
        base_limit = min(per_source, max(5, report_targets.get(source_id, 5) * 2))
        enriched_count = sum(
            item.get("content_status") in {"full_text", "partial"}
            for item, _source_rank in ranked_items
        )
        limit = max(base_limit, enriched_count)
        for rank, (item, source_rank) in enumerate(ranked_items[:limit], start=1):
            compact.append(
                {
                    **{
                        key: item.get(key)
                        for key in _CANDIDATE_FIELDS
                        if item.get(key) is not None
                    },
                    "source_candidate_rank": rank,
                    "source_rank": source_rank,
                    "previously_reported": item.get("item_id") in reported_item_ids,
                    "semantic_fingerprint": semantic_fingerprint(item),
                }
            )
    return compact


def _source_limit(
    source_configs: dict[str, Any],
    source_id: object,
    field: str,
    default: int,
) -> int:
    source = source_configs.get(str(source_id))
    return int(getattr(source, field, default))


def _balanced_source_batches(
    candidates: list[dict[str, Any]], maximum_batches: int = 3
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in candidates:
        source_id = str(item.get("source_id") or "")
        if source_id:
            counts[source_id] = counts.get(source_id, 0) + 1
    bins: list[tuple[list[str], int]] = [([], 0) for _ in range(maximum_batches)]
    for source_id, count in sorted(counts.items(), key=lambda row: (-row[1], row[0])):
        target = min(range(len(bins)), key=lambda index: (bins[index][1], index))
        source_ids, total = bins[target]
        source_ids.append(source_id)
        bins[target] = (source_ids, total + count)
    return [
        {
            "batch_id": f"brief-batch-{position}",
            "source_ids": source_ids,
            "candidate_count": count,
        }
        for position, (source_ids, count) in enumerate(bins, start=1)
        if source_ids
    ]


def _build_brief_plan(
    candidates: list[dict[str, Any]],
    source_configs: dict[str, Any],
    batches: list[dict[str, Any]],
    reusable_briefs: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    reusable_briefs = reusable_briefs or {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        source_id = str(item.get("source_id") or "")
        if source_id:
            grouped.setdefault(source_id, []).append(item)
    batch_by_source = {
        source_id: str(batch["batch_id"])
        for batch in batches
        for source_id in batch["source_ids"]
    }
    plan: list[dict[str, Any]] = []
    for source_id, items in grouped.items():
        source = source_configs.get(source_id)
        target = min(
            len(items),
            int(getattr(source, "report_target", 10)),
            int(getattr(source, "report_max", 15)),
            15,
        )
        if target <= 0:
            continue
        default_item_ids = [str(item["item_id"]) for item in items[:target]]
        reuse_item_ids = [
            item_id for item_id in default_item_ids if item_id in reusable_briefs
        ]
        first = items[0]
        module = first.get("module") or getattr(source, "module", None) or "unknown"
        category = first.get("category") or getattr(source, "category", None) or "unknown"
        plan.append(
            {
                "source_id": source_id,
                "section_id": f"{module}.{category}",
                "batch_id": batch_by_source.get(source_id),
                "target_count": target,
                "default_item_ids": default_item_ids,
                "reuse_item_ids": reuse_item_ids,
                "author_item_ids": [
                    item_id for item_id in default_item_ids if item_id not in reusable_briefs
                ],
            }
        )
    return sorted(plan, key=lambda row: (str(row["batch_id"]), str(row["source_id"])))


def build_context(
    index_path: Path,
    config: AppConfig,
    data_dir: Path,
    edition: str,
    collection_window: dict[str, str] | None = None,
) -> Path:
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("Index must be a JSON object")
    date = str(index.get("date", "unknown-date"))
    recent_reports, context_warnings, reported_item_ids = _load_reports(
        data_dir / "reports",
        date,
        edition,
    )

    state_dir = data_dir / "state"
    theses = [
        item
        for item in _read_state(state_dir / "theses.json")
        if item.get("status", "active") == "active"
    ]
    watchlist = [
        item
        for item in _read_state(state_dir / "watchlist.json")
        if item.get("status", "active") == "active"
    ]
    predictions = [
        item
        for item in _read_state(state_dir / "predictions.json")
        if item.get("status", "open") == "open"
    ]
    source_configs = {source.id: source for source in config.sources}
    candidates = _compact_candidates(
        index,
        config.budget.context_items_per_source,
        {source.id: source.report_target for source in config.sources},
        reported_item_ids,
    )
    semantic_cache = load_semantic_cache(data_dir)
    reusable_briefs = {
        str(item["item_id"]): reusable
        for item in candidates
        if (reusable := reusable_semantic_brief(item, semantic_cache)) is not None
    }
    authoring_candidates = [
        item for item in candidates if str(item.get("item_id")) not in reusable_briefs
    ]
    brief_batches = _balanced_source_batches(authoring_candidates)

    bundle = {
        "schema_version": "1.5",
        "generated_at": now_iso(config.timezone),
        "edition": edition,
        "collection_window": collection_window,
        "index_path": str(index_path.resolve()),
        "candidate_sources": [
            {
                **{
                    key: source.get(key)
                    for key in (
                        "source_id",
                        "source_name",
                        "source_url",
                        "status",
                        "error",
                        "page_results",
                    )
                },
                "report_target": _source_limit(
                    source_configs, source.get("source_id"), "report_target", 10
                ),
                "report_max": _source_limit(
                    source_configs, source.get("source_id"), "report_max", 15
                ),
            }
            for source in index.get("sources", [])
        ],
        "candidate_items": candidates,
        "reusable_briefs": list(reusable_briefs.values()),
        "brief_authoring_batches": brief_batches,
        "brief_plan": _build_brief_plan(
            candidates, source_configs, brief_batches, reusable_briefs
        ),
        "continuity_reports": recent_reports,
        "active_theses": theses,
        "active_watchlist": watchlist,
        "open_predictions": predictions,
        "user_feedback": _read_state(state_dir / "user-feedback.json"),
        "context_warnings": context_warnings,
        "content_loading_rule": (
            "Article bodies are not embedded. Read content_path only for selected evidence items; "
            "when unavailable, use only observed title/public abstract/link and never infer unseen "
            "details."
        ),
        "brief_authoring_rule": (
            "Call Hermes delegate_task once in batch mode with one model worker per "
            "brief_authoring_batch, so all batches run concurrently. Each worker must satisfy its "
            "brief_plan targets; merge reusable_briefs without rewriting them, and send only "
            "author_item_ids to workers. default_item_ids are the deterministic baseline and "
            "may be "
            "replaced only by candidates from the same source. The model itself authors every "
            "semantic field: preserve the indexed headline, naturally translate each non-Chinese "
            "headline into title_zh, and write a Chinese TL;DR from content_path when fetched, "
            "otherwise from description/public abstract, otherwise by cautiously restating only "
            "facts explicit in the title. Do not use Python/string templates or an external "
            "translation API for semantic text. Never use language/source prefixes, 'see link', "
            "'source X reported', English text with a Chinese prefix, or workflow placeholders. "
            "Workers return briefs only; the main agent merges, ranks, selects featured events, "
            "and authors analysis once. The compiler never creates missing briefs."
        ),
        "continuity_loading_rule": (
            "Use only continuity fields not listed in excluded. Reject means start fresh and "
            "retain the diagnostic only. Never copy prior formatting or unscored prose."
        ),
        "selection_rule": (
            "For every successful source, fill report_target when that many real candidates exist; "
            "do not apply an importance-score cutoff. Keep no more than report_max per source, "
            "sort displayed briefs by relative importance, and preserve source_rank for the "
            "publisher's original popularity/order. Older items remain eligible when "
            "previously_reported is false. Reserve featured events/full-text loading for evidence "
            "used in analysis."
        ),
        "budget": {
            "max_runtime_seconds": config.budget.max_runtime_seconds,
            "max_agent_tokens": config.budget.max_agent_tokens,
            "report_items_per_source": config.budget.report_items_per_source,
            "max_fulltext_per_run": config.budget.max_fulltext_per_run,
            "fulltext_global_concurrency": config.browser.global_concurrency,
            "fulltext_per_domain_concurrency": config.browser.per_domain_concurrency,
        },
    }
    context_dir = data_dir / "context" / date
    revision = next_revision(context_dir, edition)
    output = context_dir / f"{edition}-r{revision}.json"
    write_immutable_json(output, bundle)
    write_json(data_dir / "context" / f"latest-{edition}.json", bundle)
    return output
