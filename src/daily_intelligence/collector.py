from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Page, sync_playwright

from .adapters import collect_candidates
from .adapters import is_eligible as is_eligible
from .config import (
    AppConfig,
    SourceConfig,
    resolve_browser_channel,
    resolve_profile_dir,
    source_urls,
)
from .models import SourceResult, SourceStatus
from .storage import next_revision, write_immutable_json
from .utils import now_iso, read_json, timestamp_slug, today_str, write_json

CHALLENGE_TEXTS = (
    "verify you are human",
    "checking your browser",
    "are you a robot",
    "unusual traffic",
    "access denied",
    "security check",
    "enable javascript and cookies",
    "captcha",
    "robot check",
)
RATE_LIMIT_TEXTS = (
    "temporarily limited",
    "temporarily restricted",
    "too many requests",
    "rate limit exceeded",
)


def detect_challenge(page: Page, http_status: int | None) -> dict[str, Any]:
    title = ""
    body = ""
    with contextlib.suppress(Exception):
        title = page.title().lower()
    with contextlib.suppress(Exception):
        body = page.locator("body").inner_text(timeout=3000).lower()[:30000]
    rate_limited_text = next(
        (text for text in RATE_LIMIT_TEXTS if text in title or text in body), None
    )
    matched = rate_limited_text or next(
        (text for text in CHALLENGE_TEXTS if text in title or text in body), None
    )
    iframe_count = 0
    with contextlib.suppress(Exception):
        iframe_count = page.locator(
            'iframe[src*="captcha"], iframe[src*="challenge"], iframe[title*="challenge" i]'
        ).count()
    rate_limited = http_status == 429 or rate_limited_text is not None
    required = http_status in {401, 403, 429} or matched is not None or iframe_count > 0
    return {
        "required": required,
        "rate_limited": rate_limited,
        "matched_text": matched,
        "iframe_detected": iframe_count > 0,
    }


def classify_source_status(
    http_status: int | None,
    challenge_required: bool,
    has_items: bool,
    rate_limited: bool = False,
) -> SourceStatus:
    if rate_limited:
        return SourceStatus.RATE_LIMITED
    if challenge_required:
        return SourceStatus.VERIFICATION_REQUIRED
    if http_status is not None and http_status >= 400:
        return SourceStatus.FAILED
    return SourceStatus.SUCCESS if has_items else SourceStatus.NO_ITEMS


def collect_one(
    context: BrowserContext,
    source: SourceConfig,
    config: AppConfig,
    data_dir: Path,
) -> SourceResult:
    collected_at = now_iso(config.timezone)
    page = context.new_page()
    response = None
    try:
        response = page.goto(
            source.url,
            wait_until="domcontentloaded",
            timeout=config.browser.navigation_timeout_ms,
        )
        page.wait_for_timeout(source.wait_ms or config.browser.default_wait_ms)
        http_status = response.status if response else None
        return collect_loaded_page(page, source, config, http_status, collected_at)
    except Exception as exc:
        return SourceResult(
            source_id=source.id,
            source_name=source.name,
            source_url=source.url,
            status=SourceStatus.FAILED,
            collected_at=collected_at,
            module=source.module,
            category=source.category,
            page_title=_safe_title(page),
            final_url=page.url,
            http_status=response.status if response else None,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        page.close()


def collect_loaded_page(
    page: Page,
    source: SourceConfig,
    config: AppConfig,
    http_status: int | None = None,
    collected_at: str | None = None,
) -> SourceResult:
    collected_at = collected_at or now_iso(config.timezone)
    challenge = detect_challenge(page, http_status)
    page_title = _safe_title(page)
    items = [] if challenge["required"] else collect_candidates(page, source, collected_at)
    status = classify_source_status(
        http_status,
        challenge["required"],
        bool(items),
        bool(challenge.get("rate_limited")),
    )
    error = f"HTTP {http_status}" if http_status is not None and http_status >= 400 else None
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=status,
        collected_at=collected_at,
        module=source.module,
        category=source.category,
        page_title=page_title,
        final_url=page.url,
        http_status=http_status,
        error=error,
        challenge=challenge,
        items=items,
    )


def collect_source(
    context: BrowserContext,
    source: SourceConfig,
    config: AppConfig,
    data_dir: Path,
) -> SourceResult:
    page_results = [
        collect_one(context, replace(source, url=url), config, data_dir)
        for url in source_urls(source, data_dir)
    ]
    for result in page_results:
        if result.status == SourceStatus.NO_ITEMS and (
            result.error or (result.http_status is not None and result.http_status >= 400)
        ):
            result.status = SourceStatus.FAILED
    # Merge page results in round-robin order so a broad first page cannot
    # starve later BBC/Guardian sections when max_items is reached.
    seen: set[str] = set()
    items = []
    position = 0
    while len(items) < source.max_items:
        added = False
        for result in page_results:
            if position >= len(result.items):
                continue
            item = result.items[position]
            if item.canonical_url in seen:
                continue
            seen.add(item.canonical_url)
            items.append(item)
            added = True
            if len(items) >= source.max_items:
                break
        position += 1
        if not added and all(position >= len(result.items) for result in page_results):
            break
    for source_rank, item in enumerate(items, start=1):
        item.metadata["source_rank"] = source_rank
    statuses = {result.status for result in page_results}
    if items and statuses <= {SourceStatus.SUCCESS, SourceStatus.NO_ITEMS}:
        status = SourceStatus.SUCCESS
    elif items:
        status = SourceStatus.PARTIAL
    elif SourceStatus.RATE_LIMITED in statuses:
        status = SourceStatus.RATE_LIMITED
    elif SourceStatus.VERIFICATION_REQUIRED in statuses:
        status = SourceStatus.VERIFICATION_REQUIRED
    elif SourceStatus.FAILED in statuses:
        status = SourceStatus.FAILED
    else:
        status = SourceStatus.NO_ITEMS
    return SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status=status,
        collected_at=now_iso(config.timezone),
        module=source.module,
        category=source.category,
        error="; ".join(result.error for result in page_results if result.error) or None,
        page_results=[
            {
                "url": result.source_url,
                "final_url": result.final_url,
                "status": result.status,
                "http_status": result.http_status,
                "error": result.error,
                "challenge": result.challenge,
                "items_count": len(result.items),
            }
            for result in page_results
        ],
        items=items,
    )


def _safe_title(page: Page) -> str:
    try:
        return page.title()
    except Exception:
        return ""


def collect_sources(
    config: AppConfig,
    data_dir: Path,
    edition: str,
    headed: bool,
    only_source_ids: set[str] | None = None,
    profile_dir: Path | None = None,
    browser_channel: str | None = None,
    revision: int | None = None,
    temporary: bool = False,
) -> Path:
    selected = [
        source
        for source in config.sources
        if source.enabled and (not only_source_ids or source.id in only_source_ids)
    ]
    unknown_ids = (only_source_ids or set()) - {source.id for source in selected}
    if unknown_ids:
        raise ValueError(f"Unknown or disabled sources: {sorted(unknown_ids)}")

    profile = resolve_profile_dir(config, profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    channel = resolve_browser_channel(config, browser_channel)
    with sync_playwright() as playwright:
        kwargs: dict[str, Any] = {
            "user_data_dir": str(profile),
            "headless": not headed,
            "locale": "en-US",
            "timezone_id": config.timezone,
            "viewport": {"width": 1440, "height": 1000},
        }
        if channel:
            kwargs["channel"] = channel
        context = playwright.chromium.launch_persistent_context(**kwargs)
        try:
            results = [collect_source(context, source, config, data_dir) for source in selected]
        finally:
            context.close()

    return write_results_index(
        results,
        data_dir,
        edition,
        config.timezone,
        profile,
        source_policies={
            source.id: {
                "report_target": source.report_target,
                "report_max": source.report_max,
            }
            for source in selected
        },
        revision=revision,
        temporary=temporary,
    )


def write_results_index(
    results: list[SourceResult],
    data_dir: Path,
    edition: str,
    timezone: str,
    profile: Path,
    source_policies: dict[str, dict[str, int]] | None = None,
    revision: int | None = None,
    temporary: bool = False,
) -> Path:
    date = today_str(timezone)
    generated_at = now_iso(timezone)
    index_dir = data_dir / "indexes" / date

    if temporary:
        resolved_revision = 0
        output = (
            data_dir
            / "runs"
            / "retries"
            / date
            / f"{edition}-{timestamp_slug(timezone)}.json"
        )
    else:
        resolved_revision = revision or next_revision(index_dir, edition)
        output = index_dir / f"{edition}-r{resolved_revision}.json"

    payload = {
        "schema_version": "1.1",
        "index_id": (
            f"retry-{date}-{edition}-{timestamp_slug(timezone)}"
            if temporary
            else f"index-{date}-{edition}-r{resolved_revision}"
        ),
        "date": date,
        "edition": edition,
        "revision": resolved_revision,
        "generated_at": generated_at,
        "timezone": timezone,
        "browser_profile": str(profile),
        "temporary": temporary,
        "source_policies": source_policies or {},
        "sources": [result.to_dict() for result in results],
        "items": [item.to_dict() for result in results for item in result.items],
    }
    write_immutable_json(output, payload)
    if not temporary:
        write_json(data_dir / "indexes" / "latest.json", payload)
    return output


def merge_resume_index(original_path: Path, retry_path: Path, data_dir: Path) -> Path:
    original = read_json(original_path)
    retry = read_json(retry_path)
    if not isinstance(original, dict) or not isinstance(retry, dict):
        raise ValueError("Both original and retry indexes must be JSON objects")
    retried_ids = {row["source_id"] for row in retry.get("sources", [])}
    merged_sources = [
        row for row in original.get("sources", []) if row.get("source_id") not in retried_ids
    ] + retry.get("sources", [])
    merged_items = [
        item for item in original.get("items", []) if item.get("source_id") not in retried_ids
    ] + retry.get("items", [])

    date = str(original.get("date"))
    edition = str(original.get("edition"))
    index_dir = data_dir / "indexes" / date
    revision = next_revision(index_dir, edition)
    merged = dict(original)
    merged.update(
        {
            "schema_version": "1.1",
            "index_id": f"index-{date}-{edition}-r{revision}",
            "revision": revision,
            "generated_at": retry.get("generated_at"),
            "resumed_from": str(original_path.resolve()),
            "retry_artifact": str(retry_path.resolve()),
            "sources": merged_sources,
            "items": merged_items,
        }
    )
    output = index_dir / f"{edition}-r{revision}.json"
    write_immutable_json(output, merged)
    write_json(data_dir / "indexes" / "latest.json", merged)
    return output


def merge_verified_results(
    original_path: Path,
    captured: list[SourceResult],
    data_dir: Path,
) -> Path:
    original = read_json(original_path)
    if not isinstance(original, dict):
        raise ValueError("Original index must be a JSON object")
    captured_by_source: dict[str, list[SourceResult]] = {}
    for result in captured:
        captured_by_source.setdefault(result.source_id, []).append(result)

    merged_sources: list[dict[str, Any]] = []
    for row in original.get("sources", []):
        source_results = captured_by_source.get(row.get("source_id"), [])
        if not source_results:
            merged_sources.append(row)
            continue
        updated = dict(row)
        page_results = list(updated.get("page_results", []))
        nested_items = {
            item.get("item_id"): item
            for item in updated.get("items", [])
            if isinstance(item, dict) and item.get("item_id")
        }
        for result in source_results:
            page_results = [page for page in page_results if page.get("url") != result.source_url]
            page_results.append(
                {
                    "url": result.source_url,
                    "final_url": result.final_url,
                    "status": result.status,
                    "http_status": result.http_status,
                    "error": result.error,
                    "challenge": result.challenge,
                    "items_count": len(result.items),
                }
            )
            nested_items.update({item.item_id: item.to_dict() for item in result.items})
        updated["page_results"] = page_results
        updated["items"] = list(nested_items.values())
        updated["items_count"] = len(nested_items)
        for page in page_results:
            if page.get("status") == "no_items" and (
                page.get("error")
                or (
                    isinstance(page.get("http_status"), int)
                    and int(page["http_status"]) >= 400
                )
            ):
                page["status"] = "failed"
        updated["error"] = (
            "; ".join(str(page["error"]) for page in page_results if page.get("error"))
            or None
        )
        statuses = {str(page.get("status")) for page in page_results}
        if nested_items and statuses <= {"success", "no_items"}:
            updated["status"] = SourceStatus.SUCCESS
        elif nested_items:
            updated["status"] = SourceStatus.PARTIAL
        elif "rate_limited" in statuses:
            updated["status"] = SourceStatus.RATE_LIMITED
        elif "verification_required" in statuses:
            updated["status"] = SourceStatus.VERIFICATION_REQUIRED
        elif "failed" in statuses:
            updated["status"] = SourceStatus.FAILED
        else:
            updated["status"] = SourceStatus.NO_ITEMS
        merged_sources.append(updated)

    root_items = {
        item.get("item_id"): item
        for item in original.get("items", [])
        if isinstance(item, dict) and item.get("item_id")
    }
    for results in captured_by_source.values():
        for result in results:
            root_items.update({item.item_id: item.to_dict() for item in result.items})

    date = str(original["date"])
    edition = str(original["edition"])
    revision = next_revision(data_dir / "indexes" / date, edition)
    merged = dict(original)
    merged.update(
        {
            "index_id": f"index-{date}-{edition}-r{revision}",
            "revision": revision,
            "generated_at": now_iso(str(original.get("timezone", "Asia/Shanghai"))),
            "verified_from": str(original_path.resolve()),
            "sources": merged_sources,
            "items": list(root_items.values()),
        }
    )
    output = data_dir / "indexes" / date / f"{edition}-r{revision}.json"
    write_immutable_json(output, merged)
    write_json(data_dir / "indexes" / "latest.json", merged)
    return output
