import asyncio
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader

from daily_intelligence.cli import (
    build_parser,
    capture_verified_page,
    main,
    pending_verification_pages,
    run_pending_verification,
    update_verification_portal,
    wait_for_visible_verification,
    write_verification_queue,
)
from daily_intelligence.collector import collect_source, detect_challenge, merge_verified_results
from daily_intelligence.config import OutputConfig, load_config
from daily_intelligence.content import (
    _ordered_targets,
    _run_parallel_extraction,
    synchronize_nested_items,
)
from daily_intelligence.context import _continuity_entry, build_context
from daily_intelligence.local_output import render_report_html
from daily_intelligence.models import ArticleItem, SourceResult
from daily_intelligence.notion import (
    NotionPublisher,
    evaluation_to_blocks,
    parse_user_feedback,
    report_to_blocks,
    resolve_notion_mapping,
    validate_notion_schema,
)
from daily_intelligence.reporting import report_content_hash, validate_report_data
from daily_intelligence.reports import save_evaluation, save_report
from daily_intelligence.storage import next_revision, write_immutable_json
from daily_intelligence.utils import read_json, write_json
from daily_intelligence.workflow import (
    RunStatus,
    adopt_index_for_run,
    enrich_edition,
    finalize_edition,
    prepare_edition,
    schedule_independent_evaluation,
)


def _sample_report(root: Path) -> dict:
    return json.loads((root / "examples" / "sample_report.json").read_text(encoding="utf-8"))


def test_installers_sync_into_platform_hermes_skill_roots_and_exclude_repo_state():
    root = Path(__file__).resolve().parents[1]
    powershell = (root / "scripts" / "install.ps1").read_text(encoding="utf-8")
    shell = (root / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'Join-Path $env:LOCALAPPDATA "hermes"' in powershell
    assert '"skills"' in powershell
    assert '".git"' in powershell
    assert '"build"' in powershell
    assert "if (-not $sameDirectory)" in powershell
    assert "post-install artifact" in powershell
    assert '${HOME}/.hermes' in shell
    assert 'skills_root="${hermes_home}/skills"' in shell
    assert 'target_dir="${skills_root}/research/daily-intelligence"' in shell
    assert "if source != target:" in shell
    assert "shutil.copytree(source, target, ignore=ignore)" in shell
    assert "post-install artifact" in shell


def _first_report_item(report: dict) -> dict:
    return next(item for section in report["sections"] for item in section["items"])


def _index_for(report: dict, path: Path) -> Path:
    section = next(section for section in report["sections"] if section["items"])
    ref = section["items"][0]["source_refs"][0]
    payload = {
        "schema_version": "1.1",
        "index_id": "index-test",
        "date": report["date"],
        "edition": report["edition"],
        "revision": 1,
        "generated_at": report["generated_at"],
        "timezone": "Asia/Shanghai",
        "sources": [],
        "items": [
            {
                "item_id": ref["item_id"],
                "source_id": "cnbc_world",
                "source_name": "CNBC World",
                "title": ref["title"],
                "url": ref["url"],
                "canonical_url": ref["url"],
                "discovered_at": report["generated_at"],
                "module": section["module"],
                "category": section["category"],
                "published_at": f"{report['date']}T01:00:00+08:00",
            }
        ],
    }
    return write_json(path, payload)


def test_source_taxonomy_is_loaded():
    config = load_config()
    assert config.source_by_id("twz").category == "military"
    assert config.source_by_id("weibo_hot").category == "domestic"
    assert config.source_by_id("forbes").module == "information"
    assert config.source_by_id("infoq_ai").category == "news"
    assert config.source_by_id("infoq_ai").module == "technology"
    assert config.source_by_id("yicai_economy").category == "market"
    assert config.source_by_id("hacker_news").module == "technology"
    assert config.source_by_id("twz").adapter_name == "twz_index"
    assert config.source_by_id("defence_blog_aviation").adapter_name == "browser_index"
    assert config.source_by_id("hacker_news").report_target == 15
    assert config.source_by_id("bbc_world").report_target == 10
    assert config.source_by_id("twz").report_max == 15


def test_immutable_artifacts_and_revision_allocation(tmp_path: Path):
    directory = tmp_path / "indexes"
    first = directory / "morning-r1.json"
    write_immutable_json(first, {"revision": 1})

    assert next_revision(directory, "morning") == 2
    with pytest.raises(FileExistsError, match="immutable artifact"):
        write_immutable_json(first, {"revision": 2})


def test_save_report_writes_markdown_and_continuity_state(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    draft = write_json(tmp_path / "draft.json", report)
    index = _index_for(report, tmp_path / "index.json")

    artifacts = save_report(
        draft,
        index,
        tmp_path / "data",
        output_config=OutputConfig(formats=["html", "pdf"], pdf_engine="reportlab"),
    )

    saved = read_json(Path(artifacts["json_path"]))
    markdown = Path(artifacts["markdown_path"]).read_text(encoding="utf-8")
    assert saved["report_id"].endswith("-r1")
    assert "## 资讯" in markdown
    assert "## 技术" in markdown
    assert "## 研判" in markdown
    assert "### 国内新闻" in markdown
    assert "### 今日值得关注的开源项目" in markdown
    assert "#### [CNBC](https://www.cnbc.com/world/)" in markdown
    assert "**1. [人工智能竞争转向更低成本、更高效率的系统]" in markdown
    assert [line for line in markdown.splitlines() if line.startswith("## ")] == [
        "## 资讯",
        "## 技术",
        "## 研判",
        "## 质量评估与用户反馈",
    ]
    assert "反证与不确定性" in markdown
    assert "发布时间：2026-07-12" in markdown
    html = Path(artifacts["html_path"]).read_text(encoding="utf-8")
    assert "日报中心" in html
    assert "download-feedback" in html
    assert "从 AI 研究/开发工程师的角度" in html
    assert Path(artifacts["pdf_path"]).exists()
    assert len(PdfReader(artifacts["pdf_path"]).pages) >= 1
    assert Path(artifacts["local_index_path"]).exists()
    assert artifacts["pdf_engine"] == "reportlab"
    archive = Path(artifacts["local_index_path"]).read_text(encoding="utf-8")
    assert report["title"] in archive
    assert "阅读 HTML" in archive

    events = read_json(tmp_path / "data" / "state" / "events.json")
    theses = read_json(tmp_path / "data" / "state" / "theses.json")
    assert events["items"] == []
    assert theses["items"][0]["status"] == "active"


def test_report_must_match_index_edition(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    draft = write_json(tmp_path / "draft.json", report)
    index = _index_for(report, tmp_path / "index.json")
    payload = read_json(index)
    payload["edition"] = "evening"
    write_json(index, payload)

    with pytest.raises(ValueError, match="must match"):
        save_report(draft, index, tmp_path / "data")


def test_local_html_escapes_untrusted_report_text_and_urls():
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    report["title"] = "日报 </title><script>alert(1)</script>"
    item = _first_report_item(report)
    item["title"] = "标题 <img src=x onerror=alert(1)>"
    item["source_refs"][0]["url"] = "javascript:alert(1)"

    rendered = render_report_html(report)

    assert "<script>alert(1)</script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered
    assert "<img src=x onerror=alert(1)>" not in rendered
    assert 'href="javascript:' not in rendered
    assert "Content-Security-Policy" in rendered


def test_two_stage_run_reaches_completed(monkeypatch, tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(timezone="Asia/Shanghai")
    report = _sample_report(root)
    report["date"] = "2026-07-11"
    report["generated_at"] = "2026-07-11T05:48:00+08:00"
    draft = write_json(tmp_path / "draft.json", report)

    def fake_collect_sources(**kwargs):
        output = kwargs["data_dir"] / "indexes" / "2026-07-11" / "morning-r1.json"
        return _index_for(report, output)

    context_calls = []

    def fake_build_context(index_path, _config, data_dir, edition, collection_window=None):
        context_calls.append(str(index_path))
        output = data_dir / "context" / f"{edition}-{len(context_calls)}.json"
        return write_json(
            output,
            {
                "index_path": str(index_path),
                "collection_window": collection_window,
            },
        )

    extraction_calls = []

    def fake_extract_content(**kwargs):
        extraction_calls.append(kwargs["selected_ids"])
        source = read_json(kwargs["index_path"])
        source["revision"] = 2
        output = kwargs["data_dir"] / "indexes" / "2026-07-11" / "morning-r2.json"
        return write_json(output, source)

    monkeypatch.setattr("daily_intelligence.workflow.today_str", lambda _timezone: "2026-07-11")
    monkeypatch.setattr("daily_intelligence.workflow.collect_sources", fake_collect_sources)
    monkeypatch.setattr("daily_intelligence.workflow.build_context", fake_build_context)
    monkeypatch.setattr("daily_intelligence.workflow.extract_content", fake_extract_content)
    monkeypatch.setattr(
        "daily_intelligence.workflow.schedule_independent_evaluation",
        lambda *_args, **_kwargs: {"status": "scheduled", "detail": "local-eval"},
    )

    run_path = prepare_edition(config, tmp_path / "data", "morning")
    assert read_json(run_path)["status"] == RunStatus.AWAITING_SELECTION

    enrich_edition(
        run_path,
        config,
        tmp_path / "data",
        selected_ids=["cnbc_world-example"],
        max_items=40,
    )
    enriched = read_json(run_path)
    assert enriched["status"] == RunStatus.AWAITING_AUTHORING
    assert enriched["artifacts"]["index_path"].endswith("morning-r2.json")

    enrich_edition(
        run_path,
        config,
        tmp_path / "data",
        selected_ids=["second-item"],
        max_items=40,
    )
    enriched = read_json(run_path)
    assert enriched["artifacts"]["selected_item_ids"] == [
        "cnbc_world-example",
        "second-item",
    ]
    assert extraction_calls == [["cnbc_world-example"], ["second-item"]]

    finalize_edition(
        run_path,
        draft,
        tmp_path / "data",
        output_config=OutputConfig(formats=["html"]),
    )
    completed = read_json(run_path)
    assert completed["status"] == RunStatus.COMPLETED
    assert Path(completed["artifacts"]["markdown_path"]).exists()
    assert completed["artifacts"]["collection_metrics"]["candidate_count"] == 1
    assert "phase_durations_seconds" in completed["metrics"]
    assert completed["metrics"]["phase_durations_seconds"]["collection"] >= 0
    assert completed["evaluation"]["scheduler"]["status"] == "scheduled"
    assert completed["publication"] is None


def test_enrich_edition_records_only_ids_accepted_under_hard_cap(monkeypatch, tmp_path: Path):
    config = load_config()
    data_dir = tmp_path / "data"
    run_path = data_dir / "runs" / "2026-07-14" / "morning.json"
    index_path = write_json(
        data_dir / "indexes" / "2026-07-14" / "morning-r1.json",
        {"date": "2026-07-14", "edition": "morning", "items": [], "sources": []},
    )
    write_json(
        run_path,
        {
            "date": "2026-07-14",
            "edition": "morning",
            "status": RunStatus.AWAITING_SELECTION,
            "collection_window": {},
            "artifacts": {"index_path": str(index_path)},
        },
    )
    extraction_calls = []

    def fake_extract_content(**kwargs):
        extraction_calls.append(kwargs["selected_ids"])
        return kwargs["index_path"]

    def fake_build_context(index, _config, data, edition, collection_window=None):
        return write_json(data / "context" / f"{edition}.json", {"index": str(index)})

    monkeypatch.setattr("daily_intelligence.workflow.extract_content", fake_extract_content)
    monkeypatch.setattr("daily_intelligence.workflow.build_context", fake_build_context)
    requested = [f"item-{position}" for position in range(20)]

    enrich_edition(run_path, config, data_dir, requested, max_items=None)

    run = read_json(run_path)
    assert extraction_calls == [requested[:12]]
    assert run["artifacts"]["selected_item_ids"] == requested[:12]
    enrichment = run["artifacts"]["enrichment"]
    assert enrichment == {
        "requested": 20,
        "accepted": 12,
        "hard_cap": 12,
        "global_concurrency": 3,
        "per_domain_concurrency": 1,
        "successful_item_ids": [],
        "full_text_item_ids": [],
        "partial_item_ids": [],
        "unsuccessful_item_ids": requested[:12],
    }


def test_cli_exposes_two_stage_workflow():
    parser = build_parser()
    prepared = parser.parse_args(["run-edition", "--edition", "morning"])
    enriched = parser.parse_args(["enrich-edition", "--run", "run.json"])
    finalized = parser.parse_args(
        ["finalize-edition", "--run", "run.json", "--report", "draft.json"]
    )
    verification = parser.parse_args(["verify-pending", "--index", "index.json"])
    evaluation = parser.parse_args(
        ["finalize-evaluation", "--report", "report.json", "--evaluation", "eval.json"]
    )
    assert (prepared.command, enriched.command, finalized.command) == (
        "run-edition",
        "enrich-edition",
        "finalize-edition",
    )
    assert verification.command == "verify-pending"
    assert evaluation.command == "finalize-evaluation"
    assert prepared.open_verification is False
    assert parser.parse_args(
        ["run-edition", "--edition", "morning", "--open-verification"]
    ).open_verification is True
    assert parser.parse_args(
        ["run-edition", "--edition", "morning", "--unattended"]
    ).open_verification is False


def test_run_edition_can_open_interactive_verification_after_collection(
    monkeypatch, tmp_path: Path, capsys
):
    data_dir = tmp_path / "data"
    index_path = write_json(
        data_dir / "indexes" / "2026-07-17" / "morning-r1.json",
        {"date": "2026-07-17", "edition": "morning", "items": [], "sources": []},
    )
    run_path = write_json(
        data_dir / "runs" / "2026-07-17" / "morning.json",
        {
            "date": "2026-07-17",
            "edition": "morning",
            "status": RunStatus.AWAITING_SELECTION,
            "artifacts": {"index_path": str(index_path)},
        },
    )
    calls = []

    monkeypatch.setattr(
        "daily_intelligence.cli.prepare_edition",
        lambda **_kwargs: run_path,
    )

    def fake_verification(
        index,
        _config,
        data,
        profile_dir=None,
        browser_channel=None,
        timeout_seconds=300,
    ):
        calls.append(
            {
                "index": index,
                "data": data,
                "profile_dir": profile_dir,
                "browser_channel": browser_channel,
                "timeout_seconds": timeout_seconds,
            }
        )
        return {"status": "completed_without_capture", "captured_pages": 0}

    monkeypatch.setattr(
        "daily_intelligence.cli.run_pending_verification",
        fake_verification,
    )

    exit_code = main(
        [
            "--data-dir",
            str(data_dir),
            "run-edition",
            "--edition",
            "morning",
            "--open-verification",
            "--verification-timeout-seconds",
            "17",
            "--profile-dir",
            str(tmp_path / "profile"),
            "--browser-channel",
            "msedge",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["automatic_verification"]["status"] == "completed_without_capture"
    assert read_json(run_path)["automatic_verification"]["captured_pages"] == 0
    assert calls == [
        {
            "index": index_path,
            "data": data_dir.resolve(),
            "profile_dir": tmp_path / "profile",
            "browser_channel": "msedge",
            "timeout_seconds": 17,
        }
    ]


def test_run_edition_does_not_open_verification_by_default(
    monkeypatch, tmp_path: Path, capsys
):
    data_dir = tmp_path / "data"
    index_path = write_json(
        data_dir / "indexes" / "2026-07-17" / "morning-r1.json",
        {"date": "2026-07-17", "edition": "morning", "items": [], "sources": []},
    )
    run_path = write_json(
        data_dir / "runs" / "2026-07-17" / "morning.json",
        {
            "date": "2026-07-17",
            "edition": "morning",
            "status": RunStatus.AWAITING_SELECTION,
            "artifacts": {"index_path": str(index_path)},
        },
    )
    monkeypatch.setattr(
        "daily_intelligence.cli.prepare_edition",
        lambda **_kwargs: run_path,
    )

    def fail_if_opened(*_args, **_kwargs):
        raise AssertionError("verification must remain opt-in")

    monkeypatch.setattr(
        "daily_intelligence.cli.run_pending_verification",
        fail_if_opened,
    )

    exit_code = main(
        [
            "--data-dir",
            str(data_dir),
            "run-edition",
            "--edition",
            "morning",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert "automatic_verification" not in output
    assert "automatic_verification" not in read_json(run_path)


def test_pending_verification_noops_without_pending_pages(tmp_path: Path):
    data_dir = tmp_path / "data"
    index_path = write_json(
        data_dir / "indexes" / "2026-07-17" / "morning-r1.json",
        {
            "date": "2026-07-17",
            "edition": "morning",
            "sources": [{"source_id": "bbc_world", "status": "success"}],
            "items": [],
        },
    )

    result = run_pending_verification(index_path, load_config(), data_dir)

    assert result["status"] == "no_pending_pages"
    assert result["captured_pages"] == 0
    assert result["queue_html"] is None


def test_v15_report_publishes_before_evaluation_and_state_waits(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    report["schema_version"] = "1.5"
    report.pop("quality_evaluation")
    base_analysis = report["analyses"][0]
    report["analyses"] = []
    for position, domain in enumerate(("geopolitics", "ai_technology", "markets"), start=1):
        analysis = json.loads(json.dumps(base_analysis))
        analysis["analysis_id"] = f"ANALYSIS-{position}"
        analysis["domain"] = domain
        report["analyses"].append(analysis)
    for section in report["sections"]:
        section["briefs"] = []
    event = _first_report_item(report)
    section = next(section for section in report["sections"] if section["items"])
    section["briefs"] = [
        {
            "item_id": event["source_refs"][0]["item_id"],
            "title": event["title"],
            "tldr": event["tldr"],
            "importance": event["importance"],
            "status": "NEW",
            "featured_event_id": event["event_id"],
        }
    ]
    draft = write_json(tmp_path / "draft-v15.json", report)
    index_path = _index_for(report, tmp_path / "index-v15.json")
    index = read_json(index_path)
    index["sources"] = [
        {
            "source_id": "cnbc_world",
            "source_name": "CNBC",
            "source_url": "https://www.cnbc.com/world/",
            "status": "success",
        }
    ]
    index["items"][0]["content_status"] = "not_fetched"
    index["items"][0]["metadata"] = {"role": "discovery"}
    write_json(index_path, index)

    artifacts = save_report(
        draft,
        index_path,
        tmp_path / "data",
        output_config=OutputConfig(formats=["html"]),
    )

    assert artifacts["evaluation_status"] == "pending"
    assert not (tmp_path / "data" / "state" / "theses.json").exists()
    assert validate_report_data(read_json(Path(artifacts["json_path"])))[0] == []
    saved_report = read_json(Path(artifacts["json_path"]))
    dimensions = [
        "coverage",
        "importance_ordering",
        "factual_reliability",
        "summary_accuracy",
        "analysis_traceability",
        "historical_continuity",
        "readability",
        "timeliness",
        "compliance_boundaries",
    ]
    evaluation = {
        "evaluator_role": "independent",
        "evaluated_report_id": saved_report["report_id"],
        "evaluated_content_hash": report_content_hash(saved_report),
        "dimensions": [
            {"id": dimension, "score": 4, "finding": "该项整体合格，后续继续改进。"}
            for dimension in dimensions
        ],
        "total_score": 36,
        "main_defects": ["来源覆盖仍有提升空间。"],
        "insufficient_evidence": ["部分判断缺少交叉来源。"],
        "improvements": ["下一版补充更多高价值来源。"],
        "continuity_decision": "accept",
        "exclude_from_continuity": [],
    }
    evaluation_input = write_json(tmp_path / "evaluation.json", evaluation)
    run_path = write_json(
        tmp_path / "data" / "runs" / report["date"] / "morning.json",
        {
            "status": "completed",
            "artifacts": {"report_id": saved_report["report_id"]},
            "evaluation": {"status": "pending"},
        },
    )

    evaluated = save_evaluation(
        evaluation_input,
        Path(artifacts["json_path"]),
        tmp_path / "data",
        output_config=OutputConfig(formats=["html"]),
    )

    assert Path(evaluated["evaluation_path"]).exists()
    assert (tmp_path / "data" / "state" / "theses.json").exists()
    assert read_json(run_path)["evaluation"]["status"] == "completed"
    refreshed_html = Path(artifacts["html_path"]).read_text(encoding="utf-8")
    assert '<strong>36</strong><span>/ 45</span>' in refreshed_html
    assert evaluated["local_outputs"]["html_path"] == artifacts["html_path"]
    assert "独立评估结果" in json.dumps(
        evaluation_to_blocks(read_json(Path(evaluated["evaluation_path"]))),
        ensure_ascii=False,
    )


def test_republish_name_is_explicit_and_legacy_force_alias_remains():
    parser = build_parser()

    current = parser.parse_args(
        ["finalize-edition", "--run", "run.json", "--report", "draft.json", "--republish"]
    )
    legacy = parser.parse_args(
        [
            "finalize-edition",
            "--run",
            "run.json",
            "--report",
            "draft.json",
            "--force-publish",
        ]
    )

    assert current.force_publish is True
    assert legacy.force_publish is True


def test_verification_commands_have_noninteractive_visible_wait_timeout():
    parser = build_parser()

    source = parser.parse_args(["verify-source", "reuters"])
    pending = parser.parse_args(["verify-pending", "--index", "index.json"])
    automatic = parser.parse_args(
        ["run-edition", "--edition", "morning", "--open-verification"]
    )

    assert source.timeout_seconds == 300
    assert pending.timeout_seconds == 300
    assert automatic.open_verification is True
    assert automatic.verification_timeout_seconds == 180


def test_visible_verification_wait_handles_success_and_timeout(monkeypatch):
    class FakePage:
        def is_closed(self):
            return False

        def wait_for_timeout(self, _milliseconds):
            return None

    page = FakePage()
    monkeypatch.setattr(
        "daily_intelligence.verification.detect_challenge",
        lambda _page, _status: {"required": False},
    )
    captured = []
    success = wait_for_visible_verification(
        [("source", page, 200)],
        0,
        on_verified=lambda source_id, _page: captured.append(source_id),
    )
    assert success["source"]["required"] is False
    assert success["source"]["captured"] is True
    assert captured == ["source"]

    monkeypatch.setattr(
        "daily_intelligence.verification.detect_challenge",
        lambda _page, _status: {"required": True},
    )
    timeout = wait_for_visible_verification([("source", page, 200)], 0)
    assert timeout["source"]["timed_out"] is True


def test_closed_verification_tab_is_skipped_not_treated_as_success():
    class ClosedPage:
        def is_closed(self):
            return True

    result = wait_for_visible_verification([("source", ClosedPage(), 403)], 0)

    assert result["source"]["required"] is True
    assert result["source"]["closed_by_user"] is True
    assert result["source"]["skipped"] is True


def test_visible_verification_stops_immediately_when_rate_limited(monkeypatch):
    class Page:
        def is_closed(self):
            return False

    monkeypatch.setattr(
        "daily_intelligence.verification.detect_challenge",
        lambda _page, _status: {"required": True, "rate_limited": True},
    )

    result = wait_for_visible_verification([("reuters", Page(), 429)], 30)

    assert result["reuters"]["status"] == "rate_limited"
    assert result["reuters"]["stopped"] is True


def test_reuters_temporary_access_limit_is_detected_as_rate_limited():
    class Locator:
        def __init__(self, text="", count=0):
            self.text = text
            self.value = count

        def inner_text(self, timeout):
            return self.text

        def count(self):
            return self.value

    class Page:
        def title(self):
            return "Reuters"

        def locator(self, selector):
            if selector == "body":
                return Locator("Your access to Reuters has been temporarily limited.")
            return Locator(count=0)

    result = detect_challenge(Page(), 200)

    assert result["required"] is True
    assert result["rate_limited"] is True
    assert result["matched_text"] == "temporarily limited"


def test_verified_page_is_extracted_without_second_navigation(monkeypatch):
    class CurrentPage:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, milliseconds):
            self.waits.append(milliseconds)

    page = CurrentPage()
    config = load_config()
    source = config.source_by_id("bbc_world")
    expected = SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status="success",
        collected_at="2026-07-14T06:00:00+08:00",
        items=[
            ArticleItem(
                item_id="bbc-verified",
                source_id=source.id,
                source_name=source.name,
                title="A sufficiently long verified BBC headline",
                url="https://www.bbc.com/news/articles/verified",
                canonical_url="https://bbc.com/news/articles/verified",
                discovered_at="2026-07-14T06:00:00+08:00",
            )
        ],
    )
    calls = []
    monkeypatch.setattr(
        "daily_intelligence.verification.collect_loaded_page",
        lambda current, current_source, current_config, status: (
            calls.append((current, current_source, current_config, status)) or expected
        ),
    )

    result = capture_verified_page(page, source, config)

    assert result is expected
    assert page.waits
    assert calls == [(page, source, config, None)]


def test_verified_page_merge_preserves_other_failed_page_links(tmp_path: Path):
    original_path = write_json(
        tmp_path / "data" / "indexes" / "2026-07-14" / "morning-r1.json",
        {
            "date": "2026-07-14",
            "edition": "morning",
            "revision": 1,
            "timezone": "Asia/Shanghai",
            "sources": [
                {
                    "source_id": "bbc_world",
                    "source_name": "BBC",
                    "source_url": "https://www.bbc.com/news",
                    "status": "verification_required",
                    "items": [],
                    "page_results": [
                        {
                            "url": "https://www.bbc.com/news",
                            "status": "verification_required",
                        },
                        {
                            "url": "https://www.bbc.com/news/business",
                            "status": "failed",
                            "error": "HTTP 403",
                        },
                    ],
                }
            ],
            "items": [],
        },
    )
    item = ArticleItem(
        item_id="bbc-1",
        source_id="bbc_world",
        source_name="BBC",
        title="A sufficiently long BBC headline",
        url="https://www.bbc.com/news/articles/example",
        canonical_url="https://www.bbc.com/news/articles/example",
        discovered_at="2026-07-14T06:00:00+08:00",
    )
    captured = SourceResult(
        source_id="bbc_world",
        source_name="BBC",
        source_url="https://www.bbc.com/news",
        status="success",
        collected_at="2026-07-14T06:00:00+08:00",
        final_url="https://www.bbc.com/news",
        http_status=200,
        items=[item],
    )

    merged = read_json(merge_verified_results(original_path, [captured], tmp_path / "data"))

    source = merged["sources"][0]
    assert source["status"] == "partial"
    assert source["items"][0]["item_id"] == "bbc-1"
    assert any(page.get("error") == "HTTP 403" for page in source["page_results"])


def test_verification_queue_includes_failed_and_challenged_links(tmp_path: Path):
    index = {
        "date": "2026-07-15",
        "edition": "morning",
        "sources": [
            {
                "source_id": "bbc_world",
                "source_name": "BBC",
                "source_url": "https://www.bbc.com/news",
                "status": "partial",
                "page_results": [
                    {
                        "url": "https://www.bbc.com/news",
                        "status": "verification_required",
                    },
                    {
                        "url": "https://www.bbc.com/news/business",
                        "status": "failed",
                    },
                ],
            },
            {
                "source_id": "sec_edgar_latest",
                "source_name": "SEC EDGAR",
                "source_url": "https://www.sec.gov/cgi-bin/browse-edgar",
                "status": "no_items",
                "error": "HTTP 403",
                "page_results": [
                    {
                        "url": "https://www.sec.gov/cgi-bin/browse-edgar",
                        "status": "no_items",
                    }
                ],
            },
            {
                "source_id": "huggingface_papers",
                "source_name": "Hugging Face Papers",
                "source_url": "https://huggingface.co/papers/month",
                "status": "verification_required",
                "page_results": [],
            },
            {
                "source_id": "reuters",
                "source_name": "Reuters",
                "source_url": "https://www.reuters.com/",
                "status": "rate_limited",
                "page_results": [],
            },
        ],
    }

    pending = pending_verification_pages(index)
    markdown_path, html_path = write_verification_queue(tmp_path, index, pending)

    assert [item["status"] for item in pending] == [
        "verification_required",
        "failed",
        "failed",
        "verification_required",
        "rate_limited",
    ]
    assert "https://www.bbc.com/news/business" in markdown_path.read_text(encoding="utf-8")
    html = html_path.read_text(encoding="utf-8")
    assert "daily_intel_verify__bbc_world--0" in html
    assert "逐个点击链接" in html
    assert "sec.gov/cgi-bin/browse-edgar" in html
    assert "https://huggingface.co/papers\"" in html
    assert "https://huggingface.co/papers/month" not in html
    assert "未连接采集器" in html
    assert "window.verificationPortal" in html
    assert "overflow-y:scroll" in html
    assert "列表区域可独立滚动" in html
    assert "共 5 项" in html
    assert 'data-status="rate_limited"' in html
    assert "暂时限制" in html


def test_verification_portal_receives_connection_and_capture_status():
    class Portal:
        def __init__(self):
            self.calls = []

        def evaluate(self, expression, argument):
            self.calls.append((expression, argument))

    portal = Portal()

    update_verification_portal(portal, connected=True)
    update_verification_portal(
        portal,
        "huggingface_papers--0",
        "captured",
        "已提取结构化新闻条目。",
    )

    assert portal.calls[0][1] is True
    assert portal.calls[1][1] == {
        "key": "huggingface_papers--0",
        "status": "captured",
        "detail": "已提取结构化新闻条目。",
    }


def test_collect_source_never_keeps_an_access_error_as_no_items(monkeypatch, tmp_path: Path):
    config = load_config()
    source = replace(
        config.source_by_id("bbc_world"),
        url="https://www.bbc.com/news",
        explore_urls=[],
    )
    failed = SourceResult(
        source_id=source.id,
        source_name=source.name,
        source_url=source.url,
        status="no_items",
        collected_at="2026-07-15T06:00:00+08:00",
        error="HTTP 403",
    )
    monkeypatch.setattr(
        "daily_intelligence.collector.collect_one", lambda *_args, **_kwargs: failed
    )

    result = collect_source(None, source, config, tmp_path)

    assert result.status == "failed"
    assert result.page_results[0]["status"] == "failed"


def test_verified_index_reopens_published_run_as_a_report_revision(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    index_path = write_json(
        data_dir / "indexes" / "2026-07-15" / "morning-r2.json",
        {"date": "2026-07-15", "edition": "morning", "items": [], "sources": []},
    )
    old_report = data_dir / "reports" / "2026-07-15" / "morning-r1.json"
    old_index = data_dir / "indexes" / "2026-07-15" / "morning-r1.json"
    run_path = write_json(
        data_dir / "runs" / "2026-07-15" / "morning.json",
        {
            "date": "2026-07-15",
            "edition": "morning",
            "status": RunStatus.COMPLETED_PARTIAL,
            "artifacts": {
                "index_path": str(old_index),
                "report_id": "daily-2026-07-15-morning-r1",
                "json_path": str(old_report),
                "markdown_path": str(old_report.with_suffix(".md")),
                "content_hash": "abc123",
            },
            "publication": {"page_id": "notion-page", "status": "published"},
            "evaluation": {"status": "completed"},
        },
    )
    context_path = data_dir / "context" / "2026-07-15" / "morning-r2.json"
    monkeypatch.setattr(
        "daily_intelligence.workflow.build_context",
        lambda *_args, **_kwargs: context_path,
    )

    adopt_index_for_run(load_config(), data_dir, index_path)

    run = read_json(run_path)
    assert run["status"] == RunStatus.AWAITING_SELECTION
    assert run["revision_reason"] == "verified_source_supplement"
    assert run["artifacts"]["previous_report"]["report_id"].endswith("-r1")
    assert "publication" not in run
    assert "evaluation" not in run


def test_multi_page_source_merge_is_balanced(monkeypatch, tmp_path: Path):
    config = load_config()
    source = replace(
        config.source_by_id("bbc_world"),
        url="https://www.bbc.com/news",
        explore_urls=["https://www.bbc.com/news/world", "https://www.bbc.com/news/business"],
        max_items=3,
    )

    def fake_collect_one(_context, page_source, _config, _data_dir):
        slug = page_source.url.rsplit("/", 1)[-1]
        items = [
            ArticleItem(
                item_id=f"{slug}-{position}",
                source_id=source.id,
                source_name=source.name,
                title=f"A sufficiently long headline {slug} {position}",
                url=f"https://www.bbc.com/news/articles/{slug}-{position}",
                canonical_url=f"https://bbc.com/news/articles/{slug}-{position}",
                discovered_at="2026-07-14T06:00:00+08:00",
            )
            for position in range(3)
        ]
        return SourceResult(
            source_id=source.id,
            source_name=source.name,
            source_url=page_source.url,
            status="success",
            collected_at="2026-07-14T06:00:00+08:00",
            items=items,
        )

    monkeypatch.setattr("daily_intelligence.collector.collect_one", fake_collect_one)

    result = collect_source(None, source, config, tmp_path)

    assert [item.item_id for item in result.items] == ["news-0", "world-0", "business-0"]
    assert [item.metadata["source_rank"] for item in result.items] == [1, 2, 3]


def test_post_publication_evaluation_uses_bounded_retries(monkeypatch, tmp_path: Path):
    calls = []

    class Completed:
        returncode = 0
        stdout = "Created job: eval-1"
        stderr = ""

    monkeypatch.setattr(
        "daily_intelligence.workflow.subprocess.run",
        lambda command, **kwargs: calls.append((command, kwargs)) or Completed(),
    )
    monkeypatch.setattr("daily_intelligence.workflow.project_root", lambda: tmp_path)

    result = schedule_independent_evaluation(
        tmp_path / "report.json",
        tmp_path / "index.json",
        tmp_path / "data",
        "daily-2026-07-15-morning-r1",
        "abc123",
    )

    command = calls[0][0]
    assert result["status"] == "scheduled"
    assert command[:4] == ["hermes", "cron", "create", "2m"]
    assert command[command.index("--repeat") + 1] == "3"
    assert "不得要求用户点击" in command[4]
    assert "--publish" not in command[4]
    assert result["job_id"] == "eval-1"
    assert result["attempts"] == 3

    schedule_independent_evaluation(
        tmp_path / "report.json",
        tmp_path / "index.json",
        tmp_path / "data",
        "daily-2026-07-15-morning-r1",
        "abc123",
        publish_notion=True,
    )
    assert "--publish" in calls[1][0][4]


def test_finalize_publish_records_automatic_evaluator_schedule(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "data"
    report_path = write_json(
        data_dir / "reports" / "2026-07-15" / "morning-r1.json",
        {"date": "2026-07-15", "edition": "morning"},
    )
    index_path = write_json(
        data_dir / "indexes" / "2026-07-15" / "morning-r1.json",
        {"date": "2026-07-15", "edition": "morning"},
    )
    run_path = write_json(
        data_dir / "runs" / "2026-07-15" / "morning.json",
        {
            "date": "2026-07-15",
            "edition": "morning",
            "timezone": "Asia/Shanghai",
            "status": RunStatus.FINALIZING,
            "pending_sources": [],
            "artifacts": {
                "index_path": str(index_path),
                "json_path": str(report_path),
                "report_id": "daily-2026-07-15-morning-r1",
                "content_hash": "abc123",
            },
        },
    )
    monkeypatch.setattr(
        "daily_intelligence.workflow.publish_report",
        lambda *_args, **_kwargs: ("notion-page", "published"),
    )
    monkeypatch.setattr(
        "daily_intelligence.workflow.schedule_independent_evaluation",
        lambda *_args, **_kwargs: {"status": "scheduled", "detail": "job-1"},
    )

    finalize_edition(run_path, report_path, data_dir, publish=True)

    run = read_json(run_path)
    assert run["status"] == RunStatus.COMPLETED
    assert run["evaluation"]["scheduler"] == {
        "status": "scheduled",
        "detail": "job-1",
    }


def test_finalize_retries_missing_evaluator_schedule_after_completed_publish(
    monkeypatch, tmp_path: Path
):
    data_dir = tmp_path / "data"
    report_path = write_json(data_dir / "reports" / "report.json", {})
    index_path = write_json(data_dir / "indexes" / "index.json", {})
    run_path = write_json(
        data_dir / "runs" / "2026-07-15" / "morning.json",
        {
            "date": "2026-07-15",
            "edition": "morning",
            "timezone": "Asia/Shanghai",
            "status": RunStatus.COMPLETED,
            "publication": {"page_id": "page-1", "status": "published"},
            "artifacts": {
                "json_path": str(report_path),
                "index_path": str(index_path),
                "report_id": "daily-2026-07-15-morning-r1",
                "content_hash": "abc123",
            },
            "evaluation": {"status": "pending"},
        },
    )
    monkeypatch.setattr(
        "daily_intelligence.workflow.schedule_independent_evaluation",
        lambda *_args, **_kwargs: {"status": "scheduled", "detail": "job-recovered"},
    )

    finalize_edition(run_path, report_path, data_dir, publish=True)

    run = read_json(run_path)
    assert run["evaluation"]["scheduler"]["detail"] == "job-recovered"


def test_report_rejects_claimed_access_not_present_in_index(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    index_path = _index_for(report, tmp_path / "index.json")
    index = read_json(index_path)
    index["items"][0]["content_status"] = "full_text"

    errors, _warnings = validate_report_data(report, index)

    assert any("does not match index content_status" in error for error in errors)


def test_notion_blocks_include_complete_analysis():
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    _first_report_item(report)["source_refs"][0]["published_at"] = "2026-07-12T01:00:00+08:00"
    rendered = json.dumps(report_to_blocks(report), ensure_ascii=False)

    assert "重要性 82/100" in rendered
    assert "反证" in rendered
    assert "影响" in rendered
    assert "证据事件" in rendered
    assert "发布时间：2026-07-12" in rendered
    blocks = report_to_blocks(report)
    block_types = {block["type"] for block in blocks}
    assert {"callout", "table_of_contents", "table", "numbered_list_item"} <= block_types
    top_level = [
        block["heading_1"]["rich_text"][0]["text"]["content"]
        for block in blocks
        if block["type"] == "heading_1"
    ]
    assert top_level == ["资讯", "技术", "研判", "质量评估与用户反馈"]


def test_notion_user_feedback_marker_is_machine_readable():
    parsed = parse_user_feedback(
        "用户反馈|相关性=5|准确性=4|分析价值=5|整体满意度=4|补充意见=增加国内市场"
    )

    assert parsed == {
        "scores": {
            "relevance": 5,
            "accuracy": 4,
            "analysis_value": 5,
            "overall_satisfaction": 4,
        },
        "comment": "增加国内市场",
    }


def test_evening_context_loads_morning_and_continuity_state(tmp_path: Path):
    data_dir = tmp_path / "data"
    report_dir = data_dir / "reports" / "2026-07-14"
    morning = {
        "report_id": "daily-2026-07-14-morning-r1",
        "date": "2026-07-14",
        "edition": "morning",
        "generated_at": "2026-07-14T06:00:00+08:00",
        "sections": [
            {
                "items": [
                    {
                        "event_id": "event-pending-evaluation",
                        "title": "未经评估的摘要不应复用",
                        "status": "NEW",
                        "importance": 80,
                        "source_refs": [{"item_id": "source-item-1"}],
                    }
                ]
            }
        ],
        "analyses": [{"analysis_id": "analysis-pending", "claim": "未经评估的研判"}],
    }
    write_json(report_dir / "morning-r1.json", morning)
    write_json(data_dir / "state" / "theses.json", {"items": [{"analysis_id": "a1"}]})
    write_json(data_dir / "state" / "watchlist.json", {"items": [{"watch_id": "w1"}]})
    write_json(data_dir / "state" / "predictions.json", {"items": [{"prediction_id": "p1"}]})
    index_path = write_json(
        data_dir / "indexes" / "2026-07-14" / "evening-r1.json",
        {"date": "2026-07-14", "edition": "evening", "items": []},
    )

    output = build_context(index_path, load_config(), data_dir, "evening")
    context = read_json(output)

    assert context["continuity_reports"][0]["report_id"] == morning["report_id"]
    assert context["continuity_reports"][0]["reuse_status"] == "selective"
    assert context["continuity_reports"][0]["events"] == [
        {
            "event_id": "event-pending-evaluation",
            "status": "NEW",
            "source_item_ids": ["source-item-1"],
        }
    ]
    assert context["continuity_reports"][0]["analyses"] == []
    assert context["active_theses"][0]["analysis_id"] == "a1"
    assert context["active_watchlist"][0]["watch_id"] == "w1"
    assert context["open_predictions"][0]["prediction_id"] == "p1"
    assert "never infer unseen details" in context["content_loading_rule"]
    assert "candidate_index" not in context


def test_context_caps_candidates_and_rejects_contaminated_history(tmp_path: Path):
    data_dir = tmp_path / "data"
    report = {
        "report_id": "bad-report",
        "date": "2026-07-13",
        "edition": "evening",
        "generated_at": "2026-07-13T18:00:00+08:00",
        "sections": [{"items": [{"event_id": "bad-event", "title": "错误英文残留"}]}],
        "analyses": [{"analysis_id": "bad-analysis", "claim": "错误判断"}],
        "quality_evaluation": {
            "continuity_decision": "reject",
            "exclude_from_continuity": ["all"],
        },
    }
    write_json(data_dir / "reports" / "2026-07-13" / "evening-r1.json", report)
    items = [
        {
            "item_id": f"item-{position}",
            "source_id": "bbc_world",
            "source_name": "BBC",
            "title": f"新闻标题 {position}",
            "url": f"https://www.bbc.com/news/articles/{position}",
            "discovered_at": f"2026-07-14T{position % 24:02d}:00:00+08:00",
        }
        for position in range(30)
    ]
    index_path = write_json(
        data_dir / "indexes" / "2026-07-14" / "morning-r1.json",
        {"date": "2026-07-14", "edition": "morning", "items": items, "sources": []},
    )

    context = read_json(build_context(index_path, load_config(), data_dir, "morning"))

    assert len(context["candidate_items"]) == 20
    assert context["brief_authoring_batches"] == [
        {
            "batch_id": "brief-batch-1",
            "source_ids": ["bbc_world"],
            "candidate_count": 20,
        }
    ]
    assert context["brief_plan"] == [
        {
            "source_id": "bbc_world",
            "section_id": "information.international",
            "batch_id": "brief-batch-1",
            "target_count": 10,
                "default_item_ids": [f"item-{position}" for position in range(10)],
                "reuse_item_ids": [],
                "author_item_ids": [f"item-{position}" for position in range(10)],
            }
        ]
    prior = context["continuity_reports"][0]
    assert prior["reuse_status"] == "reject"
    assert prior["events"] == []
    assert prior["analyses"] == []


def test_context_overrides_a_low_score_accept_decision(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    report = json.loads(
        (root / "examples" / "sample_report.json").read_text(encoding="utf-8")
    )
    report["quality_evaluation"] = {
        "dimensions": [
            {"id": dimension, "score": 2, "finding": "该维度存在严重质量问题。"}
            for dimension in (
                "coverage",
                "importance_ordering",
                "factual_reliability",
                "summary_accuracy",
                "analysis_traceability",
                "historical_continuity",
                "readability",
                "timeliness",
                "compliance_boundaries",
            )
        ],
        "total_score": 18,
        "continuity_decision": "accept",
        "exclude_from_continuity": [],
    }
    path = tmp_path / "data" / "reports" / "2026-07-12" / "morning-r1.json"

    entry = _continuity_entry(path, report)

    assert entry["reuse_status"] == "reject"
    assert entry["excluded"] == ["all"]
    assert entry["events"] == []
    assert entry["analyses"] == []
    assert "reject all content" in entry["continuity_override"]


def test_context_keeps_enriched_items_ahead_of_later_undated_discoveries(tmp_path: Path):
    data_dir = tmp_path / "data"
    items = [
        {
            "item_id": f"bbc-{position}",
            "source_id": "bbc_world",
            "source_name": "BBC",
            "title": f"BBC headline {position} with enough detail",
            "url": f"https://www.bbc.com/news/articles/{position}",
            "discovered_at": f"2026-07-16T{position % 24:02d}:00:00+08:00",
            "content_status": "not_fetched",
            "metadata": {"source_rank": position + 1},
        }
        for position in range(30)
    ]
    items[0].update(
        {
            "content_status": "full_text",
            "content_path": "content/bbc-0.md",
            "published_at": None,
        }
    )
    index_path = write_json(
        data_dir / "indexes" / "2026-07-16" / "morning-r1.json",
        {
            "date": "2026-07-16",
            "edition": "morning",
            "items": items,
            "sources": [],
            "source_policies": {"bbc_world": {"report_target": 10}},
        },
    )

    context = read_json(build_context(index_path, load_config(), data_dir, "morning"))

    assert context["candidate_items"][0]["item_id"] == "bbc-0"
    assert context["candidate_items"][0]["content_status"] == "full_text"
    assert context["brief_plan"][0]["default_item_ids"][0] == "bbc-0"
    assert context["brief_plan"][0]["target_count"] == 10
    assert "title_zh" in context["brief_authoring_rule"]
    assert "delegate_task" in context["brief_authoring_rule"]
    assert "model itself authors every semantic field" in context["brief_authoring_rule"]
    assert "_source_rank" not in read_json(index_path)["items"][0]


def test_enriched_root_items_are_mirrored_to_legacy_nested_items():
    payload = {
        "items": [
            {
                "item_id": "item-1",
                "content_status": "full_text",
                "content_path": "content/item-1.md",
            }
        ],
        "sources": [
            {
                "source_id": "source-1",
                "items": [{"item_id": "item-1", "content_status": "not_fetched"}],
            }
        ],
    }

    synchronize_nested_items(payload)

    nested = payload["sources"][0]["items"][0]
    assert nested == payload["items"][0]
    assert nested["content_status"] == "full_text"


def test_enrichment_targets_follow_importance_order_and_hard_limit():
    items = [
        {"item_id": "low"},
        {"item_id": "high"},
        {"item_id": "medium"},
    ]

    targets = _ordered_targets(
        items,
        ["high", "medium", "high", "missing", "low"],
        max_items=2,
    )

    assert [item["item_id"] for item in targets] == ["high", "medium"]


def test_enrichment_is_parallel_across_domains_and_serial_within_domain(
    monkeypatch, tmp_path: Path
):
    config = load_config()
    config.browser.global_concurrency = 2
    config.browser.per_domain_concurrency = 1
    active = 0
    maximum_active = 0
    active_by_domain: dict[str, int] = defaultdict(int)
    maximum_by_domain: dict[str, int] = defaultdict(int)

    async def fake_extract_one(_context, item, _config, _data_dir):
        nonlocal active, maximum_active
        domain = item["url"].split("/")[2]
        active += 1
        active_by_domain[domain] += 1
        maximum_active = max(maximum_active, active)
        maximum_by_domain[domain] = max(
            maximum_by_domain[domain], active_by_domain[domain]
        )
        await asyncio.sleep(0.02)
        active_by_domain[domain] -= 1
        active -= 1

    monkeypatch.setattr("daily_intelligence.content._extract_one", fake_extract_one)
    targets = [
        {"item_id": "a1", "url": "https://a.example/1"},
        {"item_id": "a2", "url": "https://a.example/2"},
        {"item_id": "b1", "url": "https://b.example/1"},
        {"item_id": "c1", "url": "https://c.example/1"},
    ]

    asyncio.run(_run_parallel_extraction(object(), targets, config, tmp_path))

    assert maximum_active == 2
    assert maximum_by_domain["a.example"] == 1


def test_finalize_validation_failure_returns_to_awaiting_authoring(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    run_path = data_dir / "runs" / "2026-07-12" / "morning.json"
    index_path = data_dir / "indexes" / "2026-07-12" / "morning-r1.json"
    report_path = tmp_path / "draft.json"
    write_json(index_path, {"date": "2026-07-12", "edition": "morning"})
    write_json(report_path, {"date": "2026-07-12", "edition": "morning"})
    write_json(
        run_path,
        {
            "date": "2026-07-12",
            "edition": "morning",
            "status": RunStatus.AWAITING_AUTHORING,
            "artifacts": {"index_path": str(index_path)},
        },
    )
    monkeypatch.setattr(
        "daily_intelligence.workflow.save_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad access")),
    )

    with pytest.raises(ValueError, match="bad access"):
        finalize_edition(run_path, report_path, data_dir)

    run = read_json(run_path)
    assert run["status"] == RunStatus.AWAITING_AUTHORING
    assert "bad access" in run["error"]


def test_finalize_rejects_an_index_that_lost_successful_enrichment(tmp_path):
    data_dir = tmp_path / "data"
    run_path = data_dir / "runs" / "2026-07-17" / "morning.json"
    index_path = write_json(
        data_dir / "indexes" / "2026-07-17" / "morning-r2.json",
        {
            "date": "2026-07-17",
            "edition": "morning",
            "items": [
                {
                    "item_id": "bbc-lost",
                    "content_status": "metadata_only",
                    "content_path": None,
                }
            ],
        },
    )
    draft = write_json(
        tmp_path / "draft.json", {"date": "2026-07-17", "edition": "morning"}
    )
    write_json(
        run_path,
        {
            "data_root": str(data_dir.resolve()),
            "date": "2026-07-17",
            "edition": "morning",
            "status": RunStatus.AWAITING_AUTHORING,
            "artifacts": {
                "index_path": str(index_path),
                "enrichment": {"successful_item_ids": ["bbc-lost"]},
            },
        },
    )

    with pytest.raises(ValueError, match="lost previously successful full-text enrichment"):
        finalize_edition(run_path, draft, data_dir)


def _hermes_notes_publisher(root: Path) -> NotionPublisher:
    publisher = object.__new__(NotionPublisher)
    publisher.config = yaml.safe_load(
        (root / "configs" / "notion.yaml").read_text(encoding="utf-8")
    )
    publisher.schema = {
        "properties": {
            "Name": {"type": "title"},
            "Date": {"type": "date"},
            "Status": {
                "type": "status",
                "status": {
                    "options": [
                        {"name": "New"},
                        {"name": "Reviewed"},
                        {"name": "Archived"},
                    ]
                },
            },
            "Source": {"type": "select"},
            "Tags": {"type": "multi_select"},
        }
    }
    publisher.mapping = resolve_notion_mapping(publisher.config, publisher.schema)
    return publisher


def test_notion_properties_match_hermes_notes_schema():
    root = Path(__file__).resolve().parents[1]
    report = _sample_report(root)
    publisher = _hermes_notes_publisher(root)

    morning = publisher.build_properties(report)
    assert set(morning) == {"Name", "Date", "Status", "Source", "Tags"}
    assert morning["Status"] == {"status": {"name": "New"}}
    assert morning["Source"] == {"select": {"name": "Daily Intelligence"}}
    assert {item["name"] for item in morning["Tags"]["multi_select"]} == {
        "Daily Intelligence",
        "Morning",
    }

    report["edition"] = "evening"
    evening = publisher.build_properties(report)
    assert evening["Status"] == {"status": {"name": "Reviewed"}}


def test_notion_schema_mismatch_is_actionable():
    root = Path(__file__).resolve().parents[1]
    publisher = _hermes_notes_publisher(root)
    publisher.schema["properties"]["Name"] = {"type": "rich_text"}

    with pytest.raises(ValueError, match="Name.*expected title"):
        validate_notion_schema(publisher.mapping, publisher.schema)


def test_notion_auto_selects_dedicated_daily_intelligence_schema():
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "configs" / "notion.yaml").read_text(encoding="utf-8"))
    schema = {
        "properties": {
            "Title": {"type": "title"},
            "Date": {"type": "date"},
            "Version": {"type": "select"},
            "Status": {
                "type": "status",
                "status": {"options": [{"name": "Published"}, {"name": "Final"}]},
            },
            "Source Count": {"type": "number"},
            "Event Count": {"type": "number"},
            "Pending Verification": {"type": "number"},
        }
    }

    mapping = resolve_notion_mapping(config, schema)

    assert mapping["profile"] == "daily_intelligence"
    assert mapping["properties"]["title"] == "Title"
