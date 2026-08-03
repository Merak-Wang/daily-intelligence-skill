from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from .collector import collect_sources, merge_resume_index
from .config import (
    add_source_page,
    load_config,
    load_source_pages,
    remove_source_page,
    resolve_browser_channel,
    resolve_data_dir,
    resolve_hermes_home,
    resolve_profile_dir,
    validate_output_config,
)
from .content import extract_content
from .context import build_context
from .dashboard import serve_monitor
from .importer import import_legacy
from .monitor import refresh_monitor
from .notion import append_evaluation, backfill_report_images, publish_report
from .reporting import validate_report
from .reports import save_evaluation, save_report
from .runtime import bind_data_root, load_bound_data_root, require_data_root_path
from .utils import read_json, read_json_object, write_json
from .verification import (
    capture_verified_page,
    pending_verification_pages,
    run_pending_verification,
    update_verification_portal,
    wait_for_clicked_verifications,
    wait_for_visible_verification,
    write_verification_queue,
)
from .workflow import (
    accept_authoring_batch,
    accept_authoring_metrics,
    adopt_index_for_run,
    assemble_authoring,
    begin_authoring,
    complete_edition_tail,
    enrich_edition,
    finalize_edition,
    get_authoring_status,
    prefetch_authoring_media,
    prepare_authoring_analysis,
    prepare_edition,
)

__all__ = [
    "capture_verified_page",
    "pending_verification_pages",
    "run_pending_verification",
    "update_verification_portal",
    "wait_for_clicked_verifications",
    "wait_for_visible_verification",
    "write_verification_queue",
]


def _common_parser() -> argparse.ArgumentParser:
    """处理：创建所有 CLI 子命令共享的参数解析器。
    输入：
    - 无显式业务参数：不读取业务数据；仅使用本模块声明的通用命令行选项和帮助文本。
    输出：封装“创建所有 CLI 子命令共享的参数解析器”业务结果的 ``argparse.ArgumentParser`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    parser = argparse.ArgumentParser(prog="daily-intel")
    parser.add_argument("--config", type=Path, help="Path to sources.yaml")
    parser.add_argument("--data-dir", type=Path, help="Runtime data directory")
    parser.add_argument("--timezone", help="IANA timezone overriding sources.yaml")
    return parser


def load_hermes_environment() -> Path:
    """处理：读取 Hermes 环境文件中允许的变量并补入当前进程。
    输入：
    - 无显式业务参数：不接收参数；从当前进程环境和 Hermes 配置文件读取允许导入的环境变量。
    输出：指向“读取 Hermes 环境文件中允许的变量并补入当前进程”所生成、定位或确认产物的本地路径。
    """
    env_path = resolve_hermes_home() / ".env"
    load_dotenv(env_path, override=False)
    return env_path


def build_parser() -> argparse.ArgumentParser:
    """处理：构建命令行参数解析器。
    输入：
    - 无显式业务参数：不读取业务数据；按本模块注册的子命令、参数和默认入口构建解析器。
    输出：封装“构建命令行参数解析器”业务结果的 ``argparse.ArgumentParser`` 对象；
      调用方据此继续相邻阶段或识别无结果状态。
    """
    parser = _common_parser()
    sub = parser.add_subparsers(dest="command", required=True)

    data_root = sub.add_parser(
        "data-root",
        help="Show or deliberately adopt the one canonical Hermes data root",
    )
    data_root.add_argument("action", choices=["status", "adopt"])

    collect = sub.add_parser("collect", help="Collect source indexes")
    collect.add_argument("--edition", choices=["morning", "evening"], required=True)
    collect.add_argument("--headed", action="store_true")
    collect.add_argument("--profile-dir", type=Path)
    collect.add_argument("--browser-channel")
    collect.add_argument("--source", action="append", default=[])

    refresh = sub.add_parser(
        "refresh-monitor",
        help="Refresh the zero-model-token local news stream and story clusters",
    )
    refresh.add_argument("--source", action="append", default=[])
    refresh.add_argument("--bundle", action="append", default=[])
    refresh.add_argument(
        "--core-only",
        action="store_true",
        help="Refresh only the 32 newspaper sources, excluding discovery bundles",
    )
    refresh.add_argument(
        "--force",
        action="store_true",
        help="Ignore feed freshness intervals and perform conditional requests now",
    )
    refresh.add_argument(
        "--no-html-fallback",
        action="store_true",
        help="Use RSS/Atom only for this refresh",
    )

    sub.add_parser(
        "monitor-status",
        help="Show the latest local monitor summary without refreshing",
    )

    serve = sub.add_parser(
        "serve",
        aliases=["serve-monitor"],
        help="Serve the read-only local intelligence desk",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", dest="open_browser", action="store_true")
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow binding beyond localhost on a trusted network",
    )
    serve.add_argument(
        "--refresh-minutes",
        type=int,
        default=0,
        help="Refresh in the background at this interval; zero keeps the server read-only",
    )

    imported = sub.add_parser("import-legacy", help="Import the existing browser-link JSON")
    imported.add_argument("input", type=Path)
    imported.add_argument("--edition", default="imported")

    context = sub.add_parser("build-context", help="Build compact continuity context")
    context.add_argument("--index", type=Path, required=True)
    context.add_argument("--edition", choices=["morning", "evening"], required=True)
    context.add_argument("--language", choices=["zh-CN", "en"])

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
    validate.add_argument(
        "--run",
        type=Path,
        help="Use run-owned deadline coverage targets for a degraded authoring result",
    )

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

    backfill_images = sub.add_parser(
        "backfill-notion-images",
        help="Add missing report images to existing Notion story blocks",
    )
    backfill_images.add_argument("report", type=Path)
    backfill_images.add_argument("--notion-config", type=Path)

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
    run.add_argument("--language", choices=["zh-CN", "en"])
    run.add_argument("--headed", action="store_true")
    run.add_argument("--profile-dir", type=Path)
    run.add_argument("--browser-channel")
    run.add_argument("--restart", action="store_true")
    verification_mode = run.add_mutually_exclusive_group()
    verification_mode.add_argument(
        "--open-verification",
        dest="open_verification",
        action="store_true",
        default=False,
        help=(
            "Explicitly open the connected Edge verification queue after collection; "
            "this waits until the queue completes or times out"
        ),
    )
    verification_mode.add_argument(
        "--unattended",
        dest="open_verification",
        action="store_false",
        help=(
            "Compatibility flag that keeps verification windows disabled "
            "(already the default)"
        ),
    )
    run.add_argument(
        "--verification-timeout-seconds",
        type=int,
        default=180,
        help="How long an explicitly requested verification queue remains active",
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

    begin = sub.add_parser(
        "begin-authoring",
        help="Start one timed authoring session and assign deterministic batch result paths",
    )
    begin.add_argument("--run", type=Path, required=True)

    submit = sub.add_parser(
        "submit-authoring-batch",
        help="Validate and accept one assigned model-authored brief batch",
    )
    submit.add_argument("--run", type=Path, required=True)
    submit.add_argument("--batch-id", required=True)
    submit.add_argument("--result", type=Path, required=True)

    metrics = sub.add_parser(
        "record-authoring-metrics",
        help="Validate and retain bounded Hermes batch duration/API/token metrics",
    )
    metrics.add_argument("--run", type=Path, required=True)
    metrics.add_argument("--metrics", type=Path, required=True)

    authoring_status = sub.add_parser(
        "authoring-status",
        help="Show completed batches, timings, and the analysis deadline",
    )
    authoring_status.add_argument("--run", type=Path, required=True)

    prefetch_media = sub.add_parser(
        "prefetch-media",
        help="Warm report image cache while background brief batches are running",
    )
    prefetch_media.add_argument("--run", type=Path, required=True)

    prepare_analysis = sub.add_parser(
        "prepare-analysis",
        help="Merge accepted brief batches and write a compact analysis packet",
    )
    prepare_analysis.add_argument("--run", type=Path, required=True)
    prepare_analysis.add_argument(
        "--allow-degraded",
        action="store_true",
        help="After the analysis deadline, continue with completed batches and cache hits",
    )

    assemble = sub.add_parser(
        "assemble-authoring",
        help="Merge the compact analysis payload into the Python-built schema 2.0 draft",
    )
    assemble.add_argument("--run", type=Path, required=True)
    assemble.add_argument("--analysis", type=Path, required=True)

    finalize = sub.add_parser(
        "finalize-edition",
        help=(
            "Validate and persist local JSON/Markdown/HTML/PDF, then optionally publish to Notion"
        ),
    )
    finalize.add_argument("--run", type=Path, required=True)
    finalize.add_argument("--report", type=Path, required=True)
    finalize.add_argument(
        "--publish",
        action="store_true",
        help="Also publish the locally saved report to Notion",
    )
    finalize.add_argument(
        "--republish",
        "--force-publish",
        dest="force_publish",
        action="store_true",
        help="Republish an already recorded edition; never bypasses report validation",
    )
    finalize.add_argument("--notion-config", type=Path)
    finalize.add_argument(
        "--defer-tail",
        action="store_true",
        help="Return after local HTML; generate PDF, publish Notion, and schedule evaluation later",
    )

    tail = sub.add_parser(
        "complete-edition-tail",
        help="Finish deferred PDF/Notion work and schedule independent evaluation",
    )
    tail.add_argument("--run", type=Path, required=True)
    tail.add_argument("--publish", action="store_true")
    tail.add_argument("--notion-config", type=Path)

    save = sub.add_parser(
        "save-report", help="Persist JSON/Markdown and configured local reading formats"
    )
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


def _print_json(payload: object, *, indent: int | None = 2) -> None:
    """处理：以稳定的 UTF-8 友好格式向标准输出打印 JSON。
    输入：
    - ``payload``：上游传入的结构化对象；函数只读取处理说明列出的受支持字段。
    - ``indent``：命令行 JSON 输出的缩进空格数；None 生成紧凑 JSON。
    输出：不返回新数据；完成“以稳定的 UTF-8 友好格式向标准输出打印 JSON”，
      副作用限于该处理声明的受控对象或产物。
    """
    print(json.dumps(payload, ensure_ascii=False, indent=indent))


def _print_json_file(path: Path) -> None:
    """处理：读取 JSON 文件并复用统一的命令行输出格式。
    输入：
    - ``path``：当前函数要读取、校验或写入的本地文件路径。
    输出：不返回新数据；完成“读取 JSON 文件并复用统一的命令行输出格式”，
      副作用限于该处理声明的受控对象或产物。
    """
    _print_json(read_json(path))


def main(argv: list[str] | None = None) -> int:
    """处理：解析命令行参数并执行对应入口。
    输入：
    - ``argv``：命令行传入的参数序列；None 表示读取当前进程的 sys.argv。
    输出：进程退出码；0 表示检查通过，非 0 表示存在已输出的错误。
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    load_hermes_environment()
    config = load_config(args.config, timezone=args.timezone)
    if language := getattr(args, "language", None):
        config.output.language = language
        validate_output_config(config.output)
    adopting_data_root = args.command == "data-root" and args.action == "adopt"
    data_dir = resolve_data_dir(args.data_dir, allow_conflict=adopting_data_root)
    hermes_home = resolve_hermes_home()

    if args.command == "data-root":
        if args.action == "status":
            bound = load_bound_data_root(hermes_home)
            _print_json(
                {
                    "status": "bound" if bound else "unbound",
                    "data_root": str(bound or data_dir),
                    "resolved_from_current_configuration": str(data_dir),
                }
            )
            return 0
        output = bind_data_root(
            data_dir,
            hermes_home,
            adopt=True,
            timezone=config.timezone,
        )
        _print_json(output)
        return 0

    bind_data_root(data_dir, hermes_home, timezone=config.timezone)
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.command == "source-page":
        if args.action == "list":
            _print_json(load_source_pages(data_dir))
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

    if args.command == "refresh-monitor":
        output = refresh_monitor(
            config,
            data_dir,
            only_source_ids=set(args.source) or None,
            bundles=set(args.bundle) or None,
            include_discovery=not args.core_only,
            force=args.force,
            html_fallback=not args.no_html_fallback,
        )
        snapshot = read_json_object(output, "Monitor snapshot")
        _print_json(
            {
                "snapshot_path": str(output),
                "generated_at": snapshot.get("generated_at"),
                "token_usage": snapshot.get("token_usage", 0),
                **snapshot.get("summary", {}),
            }
        )
        return 0

    if args.command == "monitor-status":
        snapshot_path = data_dir / "monitor" / "snapshot.json"
        if not snapshot_path.exists():
            _print_json(
                {
                    "status": "not_initialized",
                    "next_action": "daily-intel refresh-monitor",
                }
            )
            return 1
        snapshot = read_json_object(snapshot_path, "Monitor snapshot")
        _print_json(
            {
                "status": "ready",
                "snapshot_path": str(snapshot_path),
                "generated_at": snapshot.get("generated_at"),
                "token_usage": snapshot.get("token_usage", 0),
                **snapshot.get("summary", {}),
            }
        )
        return 0

    if args.command in {"serve", "serve-monitor"}:
        serve_monitor(
            config,
            data_dir,
            host=args.host,
            port=args.port,
            open_browser=args.open_browser,
            allow_remote=args.allow_remote,
            refresh_minutes=args.refresh_minutes,
        )
        return 0

    if args.command == "import-legacy":
        output = import_legacy(args.input, config, data_dir, args.edition)
        print(output)
        return 0

    if args.command == "build-context":
        index_path = require_data_root_path(args.index, data_dir, "Context index")
        output = build_context(index_path, config, data_dir, args.edition)
        print(output)
        return 0

    if args.command == "extract-content":
        index_path = require_data_root_path(args.index, data_dir, "Content index")
        output = extract_content(
            index_path=index_path,
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
        coverage_targets = None
        index_value = args.index
        if args.run:
            validation_run_path = require_data_root_path(
                args.run,
                data_dir,
                "Validation run",
            )
            validation_run = read_json_object(validation_run_path, "Validation run")
            index_value = index_value or Path(
                str(validation_run.get("artifacts", {}).get("index_path") or "")
            )
            values = (
                validation_run.get("artifacts", {})
                .get("authoring", {})
                .get("coverage_targets")
            )
            if isinstance(values, dict):
                coverage_targets = {
                    str(key): value for key, value in values.items()
                }
        if index_value is None:
            parser.error("validate-report requires --index or --run")
        index_path = require_data_root_path(
            index_value,
            data_dir,
            "Validation index",
        )
        errors, warnings = validate_report(
            args.report,
            index_path,
            data_dir / "state" / "events.json",
            coverage_targets=coverage_targets,
        )
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        _print_json({"errors": len(errors), "warnings": len(warnings)}, indent=None)
        return 1 if errors else 0

    if args.command == "publish-notion":
        report_path = require_data_root_path(args.report, data_dir, "Published report")
        page_id, status = publish_report(
            report_path,
            data_dir=data_dir,
            force=args.force,
            config_path=args.notion_config,
        )
        _print_json({"page_id": page_id, "status": status}, indent=None)
        return 0

    if args.command == "backfill-notion-images":
        report_path = require_data_root_path(args.report, data_dir, "Published report")
        page_id, status = backfill_report_images(
            report_path,
            data_dir=data_dir,
            config_path=args.notion_config,
        )
        _print_json({"page_id": page_id, "status": status}, indent=None)
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
        run_payload = read_json_object(output, "Run manifest")
        automatic_verification = None
        index_value = run_payload.get("artifacts", {}).get("index_path")
        if args.open_verification and index_value:
            automatic_verification = run_pending_verification(
                Path(index_value),
                config,
                data_dir,
                profile_dir=args.profile_dir,
                browser_channel=args.browser_channel,
                timeout_seconds=args.verification_timeout_seconds,
            )
            run_payload = read_json_object(output, "Run manifest")
        elif args.open_verification:
            automatic_verification = {
                "status": "index_unavailable",
                "next_action": (
                    "The run has not completed collection yet; resume it before opening "
                    "interactive verification."
                ),
            }
        if automatic_verification is not None:
            run_payload["automatic_verification"] = automatic_verification
            write_json(output, run_payload)
        _print_json(run_payload)
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
        _print_json_file(output)
        return 0

    if args.command == "begin-authoring":
        output = begin_authoring(args.run, data_dir)
        _print_json_file(output)
        return 0

    if args.command == "submit-authoring-batch":
        output = accept_authoring_batch(
            args.run,
            args.batch_id,
            args.result,
            data_dir,
        )
        _print_json_file(output)
        return 0

    if args.command == "record-authoring-metrics":
        output = accept_authoring_metrics(
            args.run,
            args.metrics,
            data_dir,
        )
        _print_json_file(output)
        return 0

    if args.command == "authoring-status":
        _print_json(get_authoring_status(args.run, data_dir))
        return 0

    if args.command == "prefetch-media":
        _print_json(prefetch_authoring_media(args.run, data_dir, config.media))
        return 0

    if args.command == "prepare-analysis":
        output = prepare_authoring_analysis(
            args.run,
            data_dir,
            allow_degraded=args.allow_degraded,
        )
        _print_json_file(output)
        return 0

    if args.command == "assemble-authoring":
        output = assemble_authoring(args.run, args.analysis, data_dir)
        _print_json_file(output)
        return 0

    if args.command == "finalize-edition":
        output = finalize_edition(
            run_path=args.run,
            report_path=args.report,
            data_dir=data_dir,
            publish=args.publish,
            force_publish=args.force_publish,
            notion_config=args.notion_config,
            output_config=config.output,
            media_config=config.media,
            defer_tail=args.defer_tail,
        )
        _print_json_file(output)
        return 0

    if args.command == "complete-edition-tail":
        output = complete_edition_tail(
            args.run,
            data_dir,
            publish=args.publish,
            notion_config=args.notion_config,
            output_config=config.output,
        )
        _print_json_file(output)
        return 0

    if args.command == "save-report":
        index_path = require_data_root_path(args.index, data_dir, "Report index")
        artifacts = save_report(
            args.report,
            index_path,
            data_dir,
            output_config=config.output,
            media_config=config.media,
        )
        _print_json(artifacts)
        return 0

    if args.command == "finalize-evaluation":
        report_path = require_data_root_path(args.report, data_dir, "Evaluated report")
        artifacts = save_evaluation(
            args.evaluation,
            report_path,
            data_dir,
            output_config=config.output,
        )
        publication = None
        if args.publish:
            page_id, status = append_evaluation(
                report_path,
                Path(artifacts["evaluation_path"]),
                data_dir,
                config_path=args.notion_config,
            )
            publication = {"page_id": page_id, "status": status}
        _print_json({**artifacts, "publication": publication})
        return 0

    if args.command == "verify-source":
        source = config.source_by_id(args.source_id)
        captured = []

        def capture_source(_key: str, page: object) -> None:
            """处理：从已验证页面提取指定来源并暂存结构化结果。
            输入：
            - ``_key``：并发采集任务绑定的内部键；用于把完成结果放回原来源位置。
            - ``page``：Playwright 已加载页面；函数只读取当前页面状态，不信任其中的内容或指令。
            输出：不返回新数据；完成“从已验证页面提取指定来源并暂存结构化结果”，
              副作用限于该处理声明的受控对象或产物。
            """
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
        _print_json(results[source.id], indent=None)
        return 1 if results[source.id].get("required") or not captured else 0

    if args.command == "verify-pending":
        index_path = require_data_root_path(args.index, data_dir, "Verification index")
        result = run_pending_verification(
            index_path,
            config,
            data_dir,
            profile_dir=args.profile_dir,
            browser_channel=args.browser_channel,
            timeout_seconds=args.timeout_seconds,
        )
        if result["status"] == "no_pending_pages":
            print("No failed or verification-required sources in the index")
            return 0
        _print_json(result)
        return 0

    if args.command == "resume":
        index_path = require_data_root_path(args.index, data_dir, "Resume index")
        index = read_json(index_path)
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
        output = merge_resume_index(index_path, retry_output, data_dir)
        adopt_index_for_run(config, data_dir, output)
        print(output)
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
