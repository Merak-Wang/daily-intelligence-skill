from __future__ import annotations

import argparse
import html
import json
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .collector import (
    collect_loaded_page,
    collect_sources,
    detect_challenge,
    merge_resume_index,
    merge_verified_results,
)
from .config import (
    AppConfig,
    add_source_page,
    canonical_source_page_url,
    load_config,
    load_source_pages,
    remove_source_page,
    resolve_browser_channel,
    resolve_data_dir,
    resolve_hermes_home,
    resolve_profile_dir,
)
from .content import extract_content
from .context import build_context
from .importer import import_legacy
from .notion import append_evaluation, publish_report
from .reporting import validate_report
from .reports import save_evaluation, save_report
from .storage import write_text_atomic
from .utils import read_json
from .workflow import adopt_index_for_run, enrich_edition, finalize_edition, prepare_edition


def wait_for_visible_verification(
    pages: list[tuple[str, object, int | None]],
    timeout_seconds: int,
    on_verified: Callable[[str, object], None] | None = None,
) -> dict[str, dict]:
    deadline = time.monotonic() + timeout_seconds
    started = time.monotonic()
    refreshed_status_only: set[str] = set()
    completed: set[str] = set()
    final_results: dict[str, dict] = {}
    while True:
        for source_id, page, initial_status in pages:
            if source_id in completed:
                continue
            if page.is_closed():
                final_results[source_id] = {
                    "required": True,
                    "closed_by_user": True,
                    "skipped": True,
                }
                completed.add(source_id)
                continue
            result = detect_challenge(page, None)
            if result.get("rate_limited"):
                final_results[source_id] = {
                    **result,
                    "status": "rate_limited",
                    "stopped": True,
                }
                completed.add(source_id)
                continue
            status_only = initial_status in {401, 403, 429} and not (
                result.get("matched_text") or result.get("iframe_detected")
            )
            should_refresh = (
                status_only
                and time.monotonic() - started >= 3
                and source_id not in refreshed_status_only
            )
            if should_refresh:
                try:
                    response = page.reload(wait_until="domcontentloaded", timeout=45_000)
                    result = detect_challenge(page, response.status if response else None)
                except Exception as exc:
                    result = {
                        "required": True,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                refreshed_status_only.add(source_id)
            elif status_only:
                result["required"] = True
            if not result.get("required"):
                try:
                    if on_verified:
                        on_verified(source_id, page)
                    result["captured"] = bool(on_verified)
                except Exception as exc:
                    result["capture_error"] = f"{type(exc).__name__}: {exc}"
                completed.add(source_id)
            final_results[source_id] = result
        if len(completed) == len(pages):
            return final_results
        if time.monotonic() >= deadline:
            for source_id, _page, _status in pages:
                result = final_results.setdefault(source_id, {"required": True})
                if source_id not in completed:
                    result["timed_out"] = True
                    completed.add(source_id)
            return final_results
        open_page = next(
            (page for _source_id, page, _status in pages if not page.is_closed()),
            None,
        )
        if open_page is None:
            return final_results
        open_page.wait_for_timeout(1000)


def capture_verified_page(page: object, source: object, config: object) -> object:
    """Extract the current verified page without navigating and retriggering a challenge."""
    page.wait_for_timeout(source.wait_ms or config.browser.default_wait_ms)
    result = collect_loaded_page(page, source, config, None)
    if result.status in {"verification_required", "rate_limited", "failed"}:
        raise RuntimeError(f"Verified page remained unavailable: {result.status}")
    if not result.items:
        raise RuntimeError("Verified page loaded but no source items could be extracted")
    return result


def pending_verification_pages(index: dict) -> list[dict[str, str]]:
    """Return failed/challenged pages once, preserving source and page order."""
    pending: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in index.get("sources", []):
        if not isinstance(row, dict):
            continue
        source_id = str(row.get("source_id", ""))
        source_name = str(row.get("source_name", source_id))
        page_rows = []
        for page in row.get("page_results", []):
            if not isinstance(page, dict):
                continue
            status = str(page.get("status", ""))
            if status == "no_items" and (
                page.get("error")
                or (
                    isinstance(page.get("http_status"), int)
                    and int(page["http_status"]) >= 400
                )
            ):
                page = {**page, "status": "failed"}
                status = "failed"
            if status in {"verification_required", "rate_limited", "failed"}:
                page_rows.append(page)
        source_status = str(row.get("status", ""))
        if source_status == "no_items" and row.get("error"):
            source_status = "failed"
        if not page_rows and source_status in {
            "verification_required",
            "rate_limited",
            "failed",
        }:
            page_rows = [
                {
                    "url": row.get("source_url"),
                    "status": source_status,
                    "error": row.get("error"),
                }
            ]
        for position, page in enumerate(page_rows):
            url = canonical_source_page_url(
                source_id,
                str(page.get("url") or row.get("source_url") or ""),
            )
            if not url.startswith(("http://", "https://")) or (source_id, url) in seen:
                continue
            seen.add((source_id, url))
            key = f"{source_id}--{position}"
            pending.append(
                {
                    "key": key,
                    "target": f"daily_intel_verify__{key}",
                    "source_id": source_id,
                    "source_name": source_name,
                    "url": url,
                    "status": str(page.get("status")),
                }
            )
    return pending


def write_verification_queue(
    data_dir: Path,
    index: dict,
    pending: list[dict[str, str]],
) -> tuple[Path, Path]:
    date = str(index.get("date", "unknown-date"))
    edition = str(index.get("edition", "unknown-edition"))
    directory = data_dir / "challenges" / date
    markdown_path = directory / f"{edition}-verification-queue.md"
    html_path = directory / f"{edition}-verification-queue.html"
    markdown = [
        f"# {date} {edition} 待验证链接",
        "",
        "请在 daily-intel 打开的 Edge 队列页中点击；成功加载并提取到条目后会自动写入新索引。",
        "",
    ]
    rows = []
    for item in pending:
        initial_status = "rate_limited" if item["status"] == "rate_limited" else "not_opened"
        initial_label = "暂时限制" if initial_status == "rate_limited" else "待打开"
        initial_detail = (
            "来源正在限流；建议等待下一时段再尝试。"
            if initial_status == "rate_limited"
            else f"原状态：{item['status']}"
        )
        markdown.append(
            f"- [{item['source_name']}]({item['url']}) — {item['status']}"
        )
        rows.append(
            f'<li data-key="{html.escape(item["key"], quote=True)}" '
            f'data-status="{initial_status}">'
            f"<a href=\"{html.escape(item['url'], quote=True)}\" "
            f"target=\"{html.escape(item['target'], quote=True)}\" "
            "onclick=\"window.verificationPortal.markOpened(this.closest('li').dataset.key)\">"
            f"{html.escape(item['source_name'])}</a>"
            f'<span class="status">{initial_label}</span>'
            f'<small class="detail">{html.escape(initial_detail)}</small>'
            f"<small>{html.escape(item['url'])}</small>"
            "</li>"
        )
    page = (
        "<!doctype html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>日报待验证链接</title>\n<style>\n"
        "*{box-sizing:border-box}html,body{height:100%;margin:0;overflow:hidden}"
        "body{font-family:Segoe UI,Microsoft YaHei,sans-serif;color:#202124;"
        "background:#f7f8fa} .app{width:min(100%,1000px);height:100vh;height:100dvh;"
        "margin:0 auto;padding:12px 16px;display:flex;flex-direction:column;min-height:0}"
        ".top{flex:none;background:#f7f8fa}h1{font-size:24px;margin:0 0 8px}"
        ".panel{background:#fff;border:1px solid #e2e5e9;border-radius:12px;"
        "padding:10px 14px;margin:0 0 10px}.panel p{margin:5px 0}"
        ".help{margin-top:6px;color:#555}.help summary{cursor:pointer;font-weight:600}"
        ".queue-title{display:flex;justify-content:space-between;gap:12px;align-items:center;"
        "margin:0 2px 8px;color:#555;font-size:14px}"
        "ul#queue{flex:1;min-height:0;overflow-y:scroll;overscroll-behavior:contain;"
        "touch-action:pan-y;scrollbar-gutter:stable;padding:0 12px 72px 0;margin:0}"
        "ul#queue::-webkit-scrollbar{width:14px}ul#queue::-webkit-scrollbar-thumb{"
        "background:#aeb4bb;border:3px solid #f7f8fa;border-radius:999px}"
        "li{margin:12px 0;padding:14px;border:1px solid #ddd;border-left:5px solid #999;"
        "border-radius:10px;list-style:none;background:#fff}\n"
        "li[data-status='opened'],li[data-status='verification_required']{border-left-color:#d58b00}"
        "li[data-status='captured']{border-left-color:#16853b;background:#f3fbf5}"
        "li[data-status='loaded_but_not_extracted']{border-left-color:#b53b31}"
        "li[data-status='rate_limited']{border-left-color:#7b61a8;background:#faf7ff}"
        "a{font-size:18px;font-weight:600} .status{margin-left:10px;padding:3px 8px;"
        "border-radius:999px;background:#eef1f4;color:#333} "
        "small{display:block;margin-top:6px;color:#666;word-break:break-all}"
        ".offline{color:#a12622}.online{color:#16853b;font-weight:700}"
        "@media(max-width:560px){.app{padding:8px}h1{font-size:20px}"
        "li{padding:12px}a{font-size:16px}.status{display:inline-block;margin:6px 0 0}}\n"
        f"</style></head><body><div class=\"app\"><div class=\"top\"><h1>"
        f"{html.escape(date)} {html.escape(edition)} 待验证链接</h1>\n<div class=\"panel\">"
        "<p id=\"connection\" class=\"offline\">未连接采集器：直接打开此 HTML 只能浏览，"
        "不会自动采集。请从 verify-pending 或 run-edition --open-verification 启动。</p>"
        f"<p id=\"progress\">已采集 0 / {len(pending)}</p>"
        "<details class=\"help\"><summary>使用说明</summary><p>逐个点击链接并在打开的 "
        "Edge 标签中登录或验证。页面出现新闻列表后自动提取 JSON；暂时限制的来源不要反复刷新。"
        "</p></details></div><div class=\"queue-title\">"
        f"<strong>来源列表</strong><span>共 {len(pending)} 项，列表区域可独立滚动</span>"
        "</div></div>\n"
        f"<ul id=\"queue\">{''.join(rows)}</ul>"
        "<script>\n"
        "window.verificationPortal={connected:false,"
        "setConnected(value){this.connected=!!value;const el=document.getElementById('connection');"
        "el.className=value?'online':'offline';el.textContent=value?"
        "'采集器已连接：可以开始点击链接。':"
        "'未连接采集器：请从 daily-intel 的交互式验证命令启动。';},"
        "markOpened(key){this.setStatus(key,'opened','页面已打开，等待登录或加载…');},"
        "setStatus(key,status,detail){const row=[...document.querySelectorAll('li[data-key]')]"
        ".find(item=>item.dataset.key===key);if(!row)return;row.dataset.status=status;"
        "const names={not_opened:'待打开',opened:'等待页面',verification_required:'等待验证',"
        "rate_limited:'暂时限制',captured:'已采集',"
        "loaded_but_not_extracted:'未提取',failed:'失败'};"
        "row.querySelector('.status').textContent=names[status]||status;"
        "row.querySelector('.detail').textContent=detail||'';this.refresh();},"
        "refresh(){const rows=[...document.querySelectorAll('li[data-key]')];"
        "const done=rows.filter(row=>row.dataset.status==='captured').length;"
        "const limited=rows.filter(row=>row.dataset.status==='rate_limited').length;"
        "const remaining=rows.filter(row=>!['captured','rate_limited'].includes("
        "row.dataset.status)).length;document.getElementById('progress').textContent="
        "`已采集 ${done} · 暂时限制 ${limited} · 待处理 ${remaining} · 共 ${rows.length}`;}};"
        "window.verificationPortal.refresh();\n"
        "</script></div></body></html>"
    )
    write_text_atomic(markdown_path, "\n".join(markdown) + "\n")
    write_text_atomic(html_path, page)
    return markdown_path, html_path


def update_verification_portal(
    portal_page: object,
    key: str | None = None,
    status: str | None = None,
    detail: str | None = None,
    *,
    connected: bool | None = None,
) -> None:
    """Update the local queue UI; browser UI failures never affect collection."""
    try:
        if connected is not None:
            portal_page.evaluate(
                "value => window.verificationPortal?.setConnected(value)", connected
            )
        if key and status:
            portal_page.evaluate(
                "payload => window.verificationPortal?.setStatus("
                "payload.key, payload.status, payload.detail)",
                {"key": key, "status": status, "detail": detail or ""},
            )
    except Exception:
        return


def wait_for_clicked_verifications(
    context: object,
    portal_page: object,
    pending: list[dict[str, str]],
    timeout_seconds: int,
    on_verified: Callable[[str, object], None],
) -> dict[str, dict]:
    deadline = time.monotonic() + timeout_seconds
    by_target = {item["target"]: item for item in pending}
    first_seen: dict[str, float] = {}
    last_attempt: dict[str, float] = {}
    completed: set[str] = set()
    results = {item["key"]: {"status": "waiting_for_click"} for item in pending}
    while time.monotonic() < deadline and not portal_page.is_closed():
        for page in context.pages:
            if page is portal_page or page.is_closed():
                continue
            try:
                target = str(page.evaluate("window.name"))
            except Exception:
                continue
            item = by_target.get(target)
            if not item or item["key"] in completed or page.url in {"", "about:blank"}:
                continue
            key = item["key"]
            first_seen.setdefault(key, time.monotonic())
            update_verification_portal(
                portal_page,
                key,
                "opened",
                "页面已打开，正在等待登录、验证或新闻列表加载。",
            )
            if time.monotonic() - first_seen[key] < 2:
                continue
            challenge = detect_challenge(page, None)
            if challenge.get("rate_limited"):
                results[key] = {"status": "rate_limited", **challenge}
                update_verification_portal(
                    portal_page,
                    key,
                    "rate_limited",
                    "来源暂时限制访问；已停止本次自动重试，请等待后续时段。",
                )
                completed.add(key)
                continue
            if challenge.get("required"):
                results[key] = {"status": "verification_required", **challenge}
                update_verification_portal(
                    portal_page,
                    key,
                    "verification_required",
                    "请在该 Edge 标签中完成登录或人工验证；成功后会自动重试。",
                )
                continue
            if time.monotonic() - last_attempt.get(key, 0) < 3:
                continue
            last_attempt[key] = time.monotonic()
            try:
                on_verified(key, page)
            except Exception as exc:
                results[key] = {
                    "status": "loaded_but_not_extracted",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                update_verification_portal(
                    portal_page,
                    key,
                    "loaded_but_not_extracted",
                    "页面可以打开，但暂未找到新闻条目；可继续等待或关闭该标签。",
                )
                continue
            results[key] = {"status": "captured", "url": page.url}
            update_verification_portal(
                portal_page,
                key,
                "captured",
                "已提取并暂存结构化新闻条目。",
            )
            completed.add(key)
        if len(completed) == len(pending):
            break
        portal_page.wait_for_timeout(1000)
    for item in pending:
        result = results[item["key"]]
        if result["status"] == "waiting_for_click":
            result["status"] = "not_opened"
            update_verification_portal(
                portal_page,
                item["key"],
                "not_opened",
                "本次未打开；链接会继续保留。",
            )
    return results


def run_pending_verification(
    index_path: Path,
    config: AppConfig,
    data_dir: Path,
    profile_dir: Path | None = None,
    browser_channel: str | None = None,
    timeout_seconds: int = 300,
) -> dict[str, object]:
    """Open the connected verification portal and merge any successfully captured pages."""
    index = read_json(index_path)
    if not isinstance(index, dict):
        raise ValueError("Index must be a JSON object")
    pending_pages = pending_verification_pages(index)
    if not pending_pages:
        return {
            "status": "no_pending_pages",
            "results": {},
            "captured_pages": 0,
            "index_path": str(index_path),
            "run_path": None,
            "queue_markdown": None,
            "queue_html": None,
            "next_action": "No failed or verification-required pages need interaction.",
        }

    captured = []
    page_sources = {
        item["key"]: replace(
            config.source_by_id(item["source_id"]), url=item["url"]
        )
        for item in pending_pages
    }

    def capture_pending(key: str, page: object) -> None:
        source = page_sources[key]
        captured.append(capture_verified_page(page, source, config))

    profile = resolve_profile_dir(config, profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    channel = resolve_browser_channel(config, browser_channel)
    queue_markdown, queue_html = write_verification_queue(
        data_dir, index, pending_pages
    )
    with sync_playwright() as playwright:
        kwargs = {
            "user_data_dir": str(profile),
            "headless": False,
            "locale": "en-US",
            "timezone_id": config.timezone,
            "viewport": {"width": 1440, "height": 1000},
        }
        if channel:
            kwargs["channel"] = channel
        context = playwright.chromium.launch_persistent_context(**kwargs)
        portal_page = context.pages[0] if context.pages else context.new_page()
        try:
            portal_page.goto(queue_html.resolve().as_uri(), wait_until="domcontentloaded")
            update_verification_portal(portal_page, connected=True)
            portal_page.bring_to_front()
            print(
                f"Opened Edge verification queue with {len(pending_pages)} links. "
                "Click any link; successful pages are captured automatically. "
                "Close the queue tab when finished."
            )
            results = wait_for_clicked_verifications(
                context,
                portal_page,
                pending_pages,
                timeout_seconds,
                capture_pending,
            )
        finally:
            context.close()

    merged_index = None
    run_path = None
    if captured:
        merged_index = merge_verified_results(index_path, captured, data_dir)
        run_path = adopt_index_for_run(config, data_dir, merged_index)
    return {
        "status": "captured" if captured else "completed_without_capture",
        "results": results,
        "captured_pages": len(captured),
        "index_path": str(merged_index) if merged_index else str(index_path),
        "run_path": str(run_path) if run_path else None,
        "queue_markdown": str(queue_markdown),
        "queue_html": str(queue_html),
        "next_action": (
            "Captured pages were merged into a new index and context. Continue this "
            "Hermes task from the refreshed run manifest."
            if captured
            else "No page was captured; retain the queue links in the report."
        ),
    }


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daily-intel")
    parser.add_argument("--config", type=Path, help="Path to sources.yaml")
    parser.add_argument("--data-dir", type=Path, help="Runtime data directory")
    parser.add_argument("--timezone", help="IANA timezone overriding sources.yaml")
    return parser


def load_hermes_environment() -> Path:
    env_path = resolve_hermes_home() / ".env"
    load_dotenv(env_path, override=False)
    return env_path


def build_parser() -> argparse.ArgumentParser:
    parser = _common_parser()
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Collect source indexes")
    collect.add_argument("--edition", choices=["morning", "evening"], required=True)
    collect.add_argument("--headed", action="store_true")
    collect.add_argument("--profile-dir", type=Path)
    collect.add_argument("--browser-channel")
    collect.add_argument("--source", action="append", default=[])

    imported = sub.add_parser("import-legacy", help="Import the existing browser-link JSON")
    imported.add_argument("input", type=Path)
    imported.add_argument("--edition", default="imported")

    context = sub.add_parser("build-context", help="Build compact continuity context")
    context.add_argument("--index", type=Path, required=True)
    context.add_argument("--edition", choices=["morning", "evening"], required=True)

    content = sub.add_parser("extract-content", help="Fetch selected article bodies")
    content.add_argument("--index", type=Path, required=True)
    content.add_argument("--item-id", action="append", required=True)
    content.add_argument(
        "--max-items",
        type=int,
        help="Maximum selected bodies to fetch; defaults to the configured hard cap (12)",
    )
    content.add_argument("--headed", action="store_true")
    content.add_argument("--profile-dir", type=Path)
    content.add_argument("--browser-channel")

    validate = sub.add_parser("validate-report", help="Validate a structured report")
    validate.add_argument("report", type=Path)
    validate.add_argument("--index", type=Path)

    publish = sub.add_parser("publish-notion", help="Publish or append report to Notion")
    publish.add_argument("report", type=Path)
    publish.add_argument(
        "--republish",
        "--force",
        dest="force",
        action="store_true",
        help="Bypass duplicate-publication protection; report validation still applies",
    )
    publish.add_argument("--notion-config", type=Path)

    verify = sub.add_parser("verify-source", help="Open a source for manual verification")
    verify.add_argument("source_id")
    verify.add_argument("--profile-dir", type=Path)
    verify.add_argument("--browser-channel")
    verify.add_argument("--timeout-seconds", type=int, default=300)

    verify_pending = sub.add_parser(
        "verify-pending",
        help="Open one Edge queue for failed/challenged links and capture clicked pages",
    )
    verify_pending.add_argument("--index", type=Path, required=True)
    verify_pending.add_argument("--profile-dir", type=Path)
    verify_pending.add_argument("--browser-channel")
    verify_pending.add_argument("--timeout-seconds", type=int, default=300)

    resume = sub.add_parser("resume", help="Retry challenged or failed sources")
    resume.add_argument("--index", type=Path, required=True)
    resume.add_argument("--headed", action="store_true")
    resume.add_argument("--profile-dir", type=Path)
    resume.add_argument("--browser-channel")

    source_page = sub.add_parser(
        "source-page",
        help="List, approve, or remove Agent-discovered index pages",
    )
    source_page.add_argument("action", choices=["list", "add", "remove"])
    source_page.add_argument("--source")
    source_page.add_argument("--url")
    source_page.add_argument("--reason", default="Agent judged this page relevant")

    run = sub.add_parser("run-edition", help="Prepare an edition through authoring context")
    run.add_argument("--edition", choices=["morning", "evening"], required=True)
    run.add_argument("--headed", action="store_true")
    run.add_argument("--profile-dir", type=Path)
    run.add_argument("--browser-channel")
    run.add_argument("--restart", action="store_true")
    run.add_argument(
        "--open-verification",
        action="store_true",
        help=(
            "After collection, open the connected Edge verification queue when failed or "
            "challenged pages exist; intended for interactive desktop runs only"
        ),
    )
    run.add_argument(
        "--verification-timeout-seconds",
        type=int,
        default=180,
        help="How long the automatic interactive verification queue remains active",
    )

    enrich = sub.add_parser(
        "enrich-edition",
        help="Fetch selected bodies and refresh an edition context",
    )
    enrich.add_argument("--run", type=Path, required=True)
    enrich.add_argument("--item-id", action="append", default=[])
    enrich.add_argument(
        "--max-items",
        type=int,
        help="Maximum selected bodies to fetch; defaults to the configured hard cap (12)",
    )
    enrich.add_argument("--headed", action="store_true")
    enrich.add_argument("--profile-dir", type=Path)
    enrich.add_argument("--browser-channel")

    finalize = sub.add_parser(
        "finalize-edition",
        help="Validate, persist, update state, and optionally publish an authored report",
    )
    finalize.add_argument("--run", type=Path, required=True)
    finalize.add_argument("--report", type=Path, required=True)
    finalize.add_argument("--publish", action="store_true")
    finalize.add_argument(
        "--republish",
        "--force-publish",
        dest="force_publish",
        action="store_true",
        help="Republish an already recorded edition; never bypasses report validation",
    )
    finalize.add_argument("--notion-config", type=Path)

    save = sub.add_parser("save-report", help="Persist JSON and Markdown report revisions")
    save.add_argument("report", type=Path)
    save.add_argument("--index", type=Path, required=True)

    evaluation = sub.add_parser(
        "finalize-evaluation",
        help="Persist a post-publication independent evaluation and optionally append it to Notion",
    )
    evaluation.add_argument("--report", type=Path, required=True)
    evaluation.add_argument("--evaluation", type=Path, required=True)
    evaluation.add_argument("--publish", action="store_true")
    evaluation.add_argument("--notion-config", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_hermes_environment()
    config = load_config(args.config, timezone=args.timezone)
    data_dir = resolve_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "source-page":
        if args.action == "list":
            print(json.dumps(load_source_pages(data_dir), ensure_ascii=False, indent=2))
            return 0
        if not args.source or not args.url:
            parser.error("source-page add/remove requires --source and --url")
        output = (
            add_source_page(config, data_dir, args.source, args.url, args.reason)
            if args.action == "add"
            else remove_source_page(data_dir, args.source, args.url)
        )
        print(output)
        return 0

    if args.command == "collect":
        output = collect_sources(
            config=config,
            data_dir=data_dir,
            edition=args.edition,
            headed=args.headed,
            only_source_ids=set(args.source) or None,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
        )
        print(output)
        return 0

    if args.command == "import-legacy":
        output = import_legacy(args.input, config, data_dir, args.edition)
        print(output)
        return 0

    if args.command == "build-context":
        output = build_context(args.index, config, data_dir, args.edition)
        print(output)
        return 0

    if args.command == "extract-content":
        output = extract_content(
            index_path=args.index,
            config=config,
            data_dir=data_dir,
            selected_ids=args.item_id,
            max_items=args.max_items,
            headed=args.headed,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
        )
        print(output)
        return 0

    if args.command == "validate-report":
        errors, warnings = validate_report(
            args.report,
            args.index,
            data_dir / "state" / "events.json",
        )
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(json.dumps({"errors": len(errors), "warnings": len(warnings)}))
        return 1 if errors else 0

    if args.command == "publish-notion":
        page_id, status = publish_report(
            args.report,
            data_dir=data_dir,
            force=args.force,
            config_path=args.notion_config,
        )
        print(json.dumps({"page_id": page_id, "status": status}))
        return 0

    if args.command == "run-edition":
        output = prepare_edition(
            config=config,
            data_dir=data_dir,
            edition=args.edition,
            headed=args.headed,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
            restart=args.restart,
        )
        run_payload = read_json(output)
        automatic_verification = None
        index_value = (
            run_payload.get("artifacts", {}).get("index_path")
            if isinstance(run_payload, dict)
            else None
        )
        if args.open_verification and index_value:
            automatic_verification = run_pending_verification(
                Path(index_value),
                config,
                data_dir,
                profile_dir=args.profile_dir,
                browser_channel=args.browser_channel,
                timeout_seconds=args.verification_timeout_seconds,
            )
            run_payload = read_json(output)
        elif args.open_verification:
            automatic_verification = {
                "status": "index_unavailable",
                "next_action": (
                    "The run has not completed collection yet; resume it before opening "
                    "interactive verification."
                ),
            }
        if isinstance(run_payload, dict) and automatic_verification is not None:
            run_payload["automatic_verification"] = automatic_verification
        print(json.dumps(run_payload, ensure_ascii=False, indent=2))
        return 0

    if args.command == "enrich-edition":
        output = enrich_edition(
            run_path=args.run,
            config=config,
            data_dir=data_dir,
            selected_ids=args.item_id,
            max_items=args.max_items,
            headed=args.headed,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
        )
        print(json.dumps(read_json(output), ensure_ascii=False, indent=2))
        return 0

    if args.command == "finalize-edition":
        output = finalize_edition(
            run_path=args.run,
            report_path=args.report,
            data_dir=data_dir,
            publish=args.publish,
            force_publish=args.force_publish,
            notion_config=args.notion_config,
        )
        print(json.dumps(read_json(output), ensure_ascii=False, indent=2))
        return 0

    if args.command == "save-report":
        artifacts = save_report(args.report, args.index, data_dir)
        print(json.dumps(artifacts, ensure_ascii=False, indent=2))
        return 0

    if args.command == "finalize-evaluation":
        artifacts = save_evaluation(args.evaluation, args.report, data_dir)
        publication = None
        if args.publish:
            page_id, status = append_evaluation(
                args.report,
                Path(artifacts["evaluation_path"]),
                data_dir,
                config_path=args.notion_config,
            )
            publication = {"page_id": page_id, "status": status}
        print(json.dumps({**artifacts, "publication": publication}, ensure_ascii=False, indent=2))
        return 0

    if args.command == "verify-source":
        source = config.source_by_id(args.source_id)
        captured = []

        def capture_source(_key: str, page: object) -> None:
            captured.append(capture_verified_page(page, source, config))

        profile = resolve_profile_dir(config, args.profile_dir)
        profile.mkdir(parents=True, exist_ok=True)
        channel = resolve_browser_channel(config, args.browser_channel)
        with sync_playwright() as playwright:
            kwargs = {
                "user_data_dir": str(profile),
                "headless": False,
                "locale": "en-US",
                "timezone_id": config.timezone,
                "viewport": {"width": 1440, "height": 1000},
            }
            if channel:
                kwargs["channel"] = channel
            context = playwright.chromium.launch_persistent_context(**kwargs)
            page = context.new_page()
            response = page.goto(
                source.url,
                wait_until="domcontentloaded",
                timeout=config.browser.navigation_timeout_ms,
            )
            page.bring_to_front()
            print(
                "A visible browser is open. Complete legitimate verification; "
                "success is detected automatically. You may close the tab when finished."
            )
            results = wait_for_visible_verification(
                [(source.id, page, response.status if response else None)],
                args.timeout_seconds,
                on_verified=capture_source,
            )
            context.close()
        if captured:
            results[source.id]["items_captured"] = len(captured[0].items)
        print(json.dumps(results[source.id], ensure_ascii=False))
        return 1 if results[source.id].get("required") or not captured else 0

    if args.command == "verify-pending":
        result = run_pending_verification(
            args.index,
            config,
            data_dir,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
            timeout_seconds=args.timeout_seconds,
        )
        if result["status"] == "no_pending_pages":
            print("No failed or verification-required sources in the index")
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if args.command == "resume":
        index = read_json(args.index)
        if not isinstance(index, dict):
            raise ValueError("Index must be a JSON object")
        source_ids = {
            row["source_id"]
            for row in index.get("sources", [])
            if row.get("status") in {"verification_required", "rate_limited", "failed"}
        }
        if not source_ids:
            print("No challenged or failed sources to retry")
            return 0
        retry_output = collect_sources(
            config=config,
            data_dir=data_dir,
            edition=index.get("edition", "resume"),
            headed=args.headed,
            only_source_ids=source_ids,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
            temporary=True,
        )
        output = merge_resume_index(args.index, retry_output, data_dir)
        adopt_index_for_run(config, data_dir, output)
        print(output)
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
