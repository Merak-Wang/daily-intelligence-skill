from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .storage import next_revision, write_immutable_json
from .utils import read_json, write_json


def _items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = read_json(path)
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return [item for item in raw["items"] if isinstance(item, dict)]
    raise ValueError(f"State file must contain a list or an object with items: {path}")


def _watch_id(analysis_id: str, signal: str) -> str:
    digest = hashlib.sha256(f"{analysis_id}|{signal}".encode()).hexdigest()[:12]
    return f"WATCH-{digest}"


def update_continuity_state(
    report: dict[str, Any],
    data_dir: Path,
) -> dict[str, str]:
    state_dir = data_dir / "state"
    generated_at = report["generated_at"]
    report_id = report["report_id"]
    evaluation = report.get("quality_evaluation", {})
    excluded = set(evaluation.get("exclude_from_continuity", []))
    skip_analyses = bool(excluded & {"analyses", "all"})
    skip_events = bool(excluded & {"event_summaries", "all"})

    theses_path = state_dir / "theses.json"
    theses = {item["analysis_id"]: item for item in _items(theses_path) if item.get("analysis_id")}
    for analysis in [] if skip_analyses else report["analyses"]:
        analysis_id = analysis["analysis_id"]
        previous = theses.get(analysis_id, {})
        history = list(previous.get("history", []))
        if not any(item.get("report_id") == report_id for item in history):
            history.append(
                {
                    "report_id": report_id,
                    "generated_at": generated_at,
                    "state_change": analysis["state_change"],
                    "confidence": analysis["confidence"],
                    "evidence_event_ids": analysis["evidence_event_ids"],
                }
            )
        status = "closed" if analysis["state_change"] in {"closed", "invalidated"} else "active"
        theses[analysis_id] = {
            "analysis_id": analysis_id,
            "domain": analysis["domain"],
            "claim": analysis["claim"],
            "confidence": analysis["confidence"],
            "status": status,
            "state_change": analysis["state_change"],
            "evidence_event_ids": analysis["evidence_event_ids"],
            "counter_evidence": analysis["counter_evidence"],
            "implications": analysis["implications"],
            "watch_signals": analysis["watch_signals"],
            "first_seen_at": previous.get("first_seen_at", generated_at),
            "updated_at": generated_at,
            "last_report_id": report_id,
            "history": history,
        }

    watchlist_path = state_dir / "watchlist.json"
    watchlist = {item["watch_id"]: item for item in _items(watchlist_path) if item.get("watch_id")}
    active_watch_ids: set[str] = set()
    for analysis in [] if skip_analyses else report["analyses"]:
        if analysis["state_change"] in {"closed", "invalidated"}:
            continue
        for signal in analysis["watch_signals"]:
            watch_id = _watch_id(analysis["analysis_id"], signal)
            active_watch_ids.add(watch_id)
            previous = watchlist.get(watch_id, {})
            watchlist[watch_id] = {
                "watch_id": watch_id,
                "analysis_id": analysis["analysis_id"],
                "signal": signal,
                "status": "active",
                "first_seen_at": previous.get("first_seen_at", generated_at),
                "updated_at": generated_at,
                "last_report_id": report_id,
            }
    current_analysis_ids = {
        analysis["analysis_id"] for analysis in ([] if skip_analyses else report["analyses"])
    }
    for watch_id, item in watchlist.items():
        if item.get("analysis_id") in current_analysis_ids and watch_id not in active_watch_ids:
            item["status"] = "closed"
            item["updated_at"] = generated_at
            item["last_report_id"] = report_id

    events_path = state_dir / "events.json"
    events = {item["event_id"]: item for item in _items(events_path) if item.get("event_id")}
    for section in [] if skip_events else report["sections"]:
        for event in section["items"]:
            event_id = event["event_id"]
            previous = events.get(event_id, {})
            report_ids = list(previous.get("report_ids", []))
            if report_id not in report_ids:
                report_ids.append(report_id)
            events[event_id] = {
                "event_id": event_id,
                "module": section["module"],
                "category": section["category"],
                "title": event["title"],
                "status": event["status"],
                "importance": event["importance"],
                "confidence": event["confidence"],
                "source_item_ids": [ref["item_id"] for ref in event["source_refs"]],
                "tags": event["tags"],
                "first_seen_at": previous.get("first_seen_at", generated_at),
                "updated_at": generated_at,
                "last_report_id": report_id,
                "report_ids": report_ids,
            }

    payloads = {
        "theses": {
            "schema_version": "1.0",
            "updated_at": generated_at,
            "items": sorted(theses.values(), key=lambda item: item["analysis_id"]),
        },
        "watchlist": {
            "schema_version": "1.0",
            "updated_at": generated_at,
            "items": sorted(watchlist.values(), key=lambda item: item["watch_id"]),
        },
        "events": {
            "schema_version": "1.0",
            "updated_at": generated_at,
            "items": sorted(events.values(), key=lambda item: item["event_id"]),
        },
    }
    outputs: dict[str, str] = {}
    for name, payload in payloads.items():
        current_path = state_dir / f"{name}.json"
        write_json(current_path, payload)
        history_dir = state_dir / "history" / name
        revision = next_revision(history_dir, report["date"])
        history_path = history_dir / f"{report['date']}-r{revision}.json"
        write_immutable_json(history_path, payload)
        outputs[name] = str(current_path)
    return outputs
