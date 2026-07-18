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
)
from .content import extract_content
from .context import build_context
from .importer import import_legacy
from .notion import append_evaluation, publish_report
from .reporting import validate_report
from .reports import save_evaluation, save_report
from .runtime import bind_data_root, load_bound_data_root, require_data_root_path
from .utils import read_json, write_json
from .verification import (
    capture_verified_page,
    pending_verification_pages,
    run_pending_verification,
    update_verification_portal,
    wait_for_clicked_verifications,
    wait_for_visible_verification,
    write_verification_queue,
)
from .workflow import adopt_index_for_run, enrich_edition, finalize_edition, prepare_edition

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
    verification_mode = run.add_mutually_exclusive_group()
    verification_mode.add_argument(
        "--open-verification",
        dest="open_verification",
        action="store_true",
        default=True,
        help=(
            "Open the connected Edge verification queue after collection when needed "
            "(default for interactive runs)"
        ),
    )
    verification_mode.add_argument(
        "--unattended",
        dest="open_verification",
        action="store_false",
        help="Never wait for a verification window; required for cron and gateway runs",
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_hermes_environment()
    config = load_config(args.config, timezone=args.timezone)
    adopting_data_root = args.command == "data-root" and args.action == "adopt"
    data_dir = resolve_data_dir(args.data_dir, allow_conflict=adopting_data_root)
    hermes_home = resolve_hermes_home()

    if args.command == "data-root":
        if args.action == "status":
            bound = load_bound_data_root(hermes_home)
            print(
                json.dumps(
                    {
                        "status": "bound" if bound else "unbound",
                        "data_root": str(bound or data_dir),
                        "resolved_from_current_configuration": str(data_dir),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        output = bind_data_root(
            data_dir,
            hermes_home,
            adopt=True,
            timezone=config.timezone,
        )
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    bind_data_root(data_dir, hermes_home, timezone=config.timezone)
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
        index_path = require_data_root_path(args.index, data_dir, "Validation index")
        errors, warnings = validate_report(
            args.report,
            index_path,
            data_dir / "state" / "events.json",
        )
        for warning in warnings:
            print(f"WARNING: {warning}")
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(json.dumps({"errors": len(errors), "warnings": len(warnings)}))
        return 1 if errors else 0

    if args.command == "publish-notion":
        report_path = require_data_root_path(args.report, data_dir, "Published report")
        page_id, status = publish_report(
            report_path,
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
            write_json(output, run_payload)
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
            output_config=config.output,
        )
        print(json.dumps(read_json(output), ensure_ascii=False, indent=2))
        return 0

    if args.command == "save-report":
        index_path = require_data_root_path(args.index, data_dir, "Report index")
        artifacts = save_report(args.report, index_path, data_dir, output_config=config.output)
        print(json.dumps(artifacts, ensure_ascii=False, indent=2))
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
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
