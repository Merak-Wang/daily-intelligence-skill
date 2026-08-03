from __future__ import annotations

import html
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from playwright.sync_api import sync_playwright

from .collector import collect_loaded_page, detect_challenge, merge_verified_results
from .config import (
    AppConfig,
    canonical_source_page_url,
    resolve_browser_channel,
    resolve_profile_dir,
)
from .storage import write_text_atomic
from .utils import read_json
from .workflow import adopt_index_for_run


def wait_for_visible_verification(
    pages: list[tuple[str, object, int | None]],
    timeout_seconds: int,
    on_verified: Callable[[str, object], None] | None = None,
) -> dict[str, dict]:
    """处理：轮询已打开页面，等待挑战消失后触发已验证采集。
    输入：
    - ``pages``：等待人工验证的一组浏览器页面及其来源身份和初始 HTTP 状态。
    - ``timeout_seconds``：允许等待整个交互或网络流程的总秒数。
    - ``on_verified``：页面验证通过后执行结构化采集的回调。
    输出：“轮询已打开页面，等待挑战消失后触发已验证采集”形成的结构化字典；
      典型键包括 closed_by_user、error、required、skipped、status、stopped。
    """
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
    """处理：直接提取当前已验证页面，不重新导航或再次触发挑战。
    输入：
    - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
    - ``source``：来源配置；包含来源 ID、名称、入口 URL、分类、过滤规则、限额和可信层级。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    输出：封装“直接提取当前已验证页面，不重新导航或再次触发挑战”业务结果的 ``object`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    page.wait_for_timeout(source.wait_ms or config.browser.default_wait_ms)
    result = collect_loaded_page(page, source, config, None)
    if result.status in {"verification_required", "rate_limited", "failed"}:
        raise RuntimeError(f"Verified page remained unavailable: {result.status}")
    if not result.items:
        raise RuntimeError("Verified page loaded but no source items could be extracted")
    return result


def pending_verification_pages(index: dict) -> list[dict[str, str]]:
    """处理：按来源和页面顺序返回去重后的失败或待验证页面。
    输入：
    - ``index``：当前来源索引对象；包含规范条目、来源结果、策略和采集时间。
    输出：“按来源和页面顺序返回去重后的失败或待验证页面”得到的有序结构化记录；
      典型字段包括 error、key、source_id、source_name、status、target、url，可直接交给下一阶段。
    """
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
                # 带错误或 HTTP 失败的旧 no_items 记录属于访问失败，必须恢复真实语义。
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
    """处理：把待验证来源写成可交互 HTML 队列和 Markdown 清单。
    输入：
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``index``：当前来源索引对象；包含规范条目、来源结果、策略和采集时间。
    - ``pending``：等待人工验证的来源页面记录；包含 source_id、名称、URL 和挑战详情。
    输出：“把待验证来源写成可交互 HTML 队列和 Markdown 清单”得到的固定结构结果；
      返回位置依次对应 markdown_path、html_path。
    """
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
        markdown.append(f"- [{item['source_name']}]({item['url']}) — {item['status']}")
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
    """处理：更新本地验证队列界面，界面失败不得影响采集。
    输入：
    - ``portal_page``：浏览器中打开的本地验证队列页面；仅用于展示状态和接收点击事件。
    - ``key``：当前集合、登记表或界面记录使用的稳定键。
    - ``status``：当前操作或来源状态；值必须属于对应的显式状态模型。
    - ``detail``：验证队列界面展示的当前操作或错误详情。
    - ``connected``：验证队列是否已连接到活动浏览器监听器。
    输出：不返回新数据；完成“更新本地验证队列界面，界面失败不得影响采集”，
      副作用限于该处理声明的受控对象或产物。
    """
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
        # 队列页只是交互投影；其脚本或标签关闭不能反向破坏采集状态。
        return


def wait_for_clicked_verifications(
    context: object,
    portal_page: object,
    pending: list[dict[str, str]],
    timeout_seconds: int,
    on_verified: Callable[[str, object], None],
) -> dict[str, dict]:
    """处理：监听队列打开的标签页，完成验证后自动采集结构化条目。
    输入：
    - ``context``：浏览器、写作或报告上下文对象；包含当前阶段已经绑定的受控状态。
    - ``portal_page``：浏览器中打开的本地验证队列页面；仅用于展示状态和接收点击事件。
    - ``pending``：等待人工验证的来源页面记录；包含 source_id、名称、URL 和挑战详情。
    - ``timeout_seconds``：允许等待整个交互或网络流程的总秒数。
    - ``on_verified``：页面验证通过后执行结构化采集的回调。
    输出：“监听队列打开的标签页，完成验证后自动采集结构化条目”形成的结构化字典；
      典型键包括 error、status、url。
    """
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
            # 只有提取并暂存结构化条目成功后才标记 captured，页面可见本身不算成功。
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
    """处理：打开连接式验证入口，并合并成功捕获的页面。
    输入：
    - ``index_path``：版本化来源索引 JSON 路径；包含根级规范 items 和来源采集状态。
    - ``config``：已校验的应用配置；提供时区、来源策略、并发限制、预算和输出选项。
    - ``data_dir``：当前运行的唯一数据根；所有状态和版本化产物都必须位于其中。
    - ``profile_dir``：持久化浏览器 Profile 目录；保存用户已授权的浏览器会话。
    - ``browser_channel``：Playwright 浏览器通道名称；为空时使用配置或默认 Chromium。
    - ``timeout_seconds``：允许等待整个交互或网络流程的总秒数。
    输出：“打开连接式验证入口，并合并成功捕获的页面”形成的结构化字典；
      典型键包括 captured_pages、headless、height、index_path、locale、next_action、queue_html、
      queue_markdown、results、run_path、status、timezone_id。
    """
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
        item["key"]: replace(config.source_by_id(item["source_id"]), url=item["url"])
        for item in pending_pages
    }

    def capture_pending(key: str, page: object) -> None:
        """处理：采集一个已由用户完成验证的待处理页面。
        输入：
        - ``key``：当前集合、登记表或界面记录使用的稳定键。
        - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
        输出：不返回新数据；完成“采集一个已由用户完成验证的待处理页面”，
          副作用限于该处理声明的受控对象或产物。
        """
        source = page_sources[key]
        captured.append(capture_verified_page(page, source, config))

    profile = resolve_profile_dir(config, profile_dir)
    profile.mkdir(parents=True, exist_ok=True)
    channel = resolve_browser_channel(config, browser_channel)
    queue_markdown, queue_html = write_verification_queue(data_dir, index, pending_pages)
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
