import json
from pathlib import Path

import pytest

from daily_intelligence.config import load_config
from daily_intelligence.notion import append_evaluation, publish_report


def test_timezone_override_is_applied():
    assert load_config(timezone="UTC").timezone == "UTC"


def test_interrupted_notion_publish_resumes_from_saved_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    root = Path(__file__).resolve().parents[2]
    report_path = root / "examples" / "sample_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    registry_key = f"{report['date']}:{report['edition']}"
    starts: list[int] = []

    class FakePublisher:
        attempts = 0
        uploads = 0

        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

        def find_page(self, _report_date):
            return "page-1"

        def create_page(self, _report):
            return "page-1"

        def update_properties(self, _page_id, _report):
            pass

        def upload_file(self, upload_path, content_type):
            assert upload_path.suffix == ".html"
            assert content_type == "text/html"
            portable = upload_path.read_text(encoding="utf-8")
            assert "analysis-notebook" in portable
            assert "file://" not in portable
            FakePublisher.uploads += 1
            return "html-upload-1"

        def retrieve_file_upload(self, file_upload_id):
            assert file_upload_id == "html-upload-1"
            return {"id": file_upload_id, "status": "uploaded"}

        def append_blocks(self, _page_id, blocks, start_block=0, on_progress=None):
            starts.append(start_block)
            if FakePublisher.attempts == 0:
                FakePublisher.attempts += 1
                raise RuntimeError("simulated attachment failure")
            assert len(blocks) == 1
            assert blocks[0]["type"] == "file"
            assert blocks[0]["file"]["type"] == "file_upload"
            assert blocks[0]["file"]["file_upload"]["id"] == "html-upload-1"
            on_progress(len(blocks))
            return len(blocks)

    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "test-source")
    monkeypatch.setattr("daily_intelligence.notion.NotionPublisher", FakePublisher)
    monkeypatch.setattr(
        "daily_intelligence.notion.report_to_blocks",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("HTML attachment mode must not publish rich-text report blocks")
        ),
    )

    with pytest.raises(RuntimeError, match="attachment failure"):
        publish_report(report_path, tmp_path)

    registry_path = tmp_path / "publishing" / "notion-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entry = registry[registry_key]
    assert entry["status"] == "publishing"
    assert entry["publication_mode"] == "html_attachment_v1"
    assert entry["blocks_appended"] == 0
    assert entry["html_upload"]["id"] == "html-upload-1"

    page_id, status = publish_report(report_path, tmp_path)

    assert (page_id, status) == ("page-1", "published")
    assert starts == [0, 0]
    assert FakePublisher.uploads == 1
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry[registry_key]["status"] == "complete"
    assert registry[registry_key]["blocks_appended"] == 1

    assert publish_report(report_path, tmp_path) == ("page-1", "skipped_duplicate")
    assert starts == [0, 0]
    assert FakePublisher.uploads == 1


def test_notion_evaluation_publishes_an_updated_html_attachment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    root = Path(__file__).resolve().parents[2]
    report_path = root / "examples" / "sample_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    evaluation = dict(report["quality_evaluation"])
    evaluation["evaluation_id"] = "evaluation-daily-2026-07-12-morning-r1-r1"
    evaluation_path = tmp_path / "evaluation.json"
    evaluation_path.write_text(
        json.dumps(evaluation, ensure_ascii=False),
        encoding="utf-8",
    )
    registry_path = tmp_path / "publishing" / "notion-registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                f"{report['date']}:{report['edition']}": {
                    "page_id": "page-1",
                    "report_id": report["report_id"],
                    "status": "complete",
                    "publication_mode": "html_attachment_v1",
                }
            }
        ),
        encoding="utf-8",
    )

    class FakePublisher:
        uploads = 0
        appended: list[dict] = []

        def __init__(self, *_args, **_kwargs):
            pass

        def close(self):
            pass

        def upload_file(self, upload_path, content_type):
            assert evaluation["evaluation_id"] in upload_path.name
            assert content_type == "text/html"
            rendered = upload_path.read_text(encoding="utf-8")
            assert "<strong>34</strong><span>/ 45</span>" in rendered
            assert "固定栏目完整" in rendered
            FakePublisher.uploads += 1
            return "evaluation-html-upload"

        def append_blocks(self, page_id, blocks):
            assert page_id == "page-1"
            assert len(blocks) == 1
            FakePublisher.appended.append(blocks[0])
            return 1

    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "test-source")
    monkeypatch.setattr("daily_intelligence.notion.NotionPublisher", FakePublisher)
    monkeypatch.setattr(
        "daily_intelligence.notion.validate_evaluation_data",
        lambda _evaluation, _report: [],
    )

    first = append_evaluation(report_path, evaluation_path, tmp_path)
    second = append_evaluation(report_path, evaluation_path, tmp_path)

    assert first == ("page-1", "html_attached")
    assert second == ("page-1", "skipped_duplicate")
    assert FakePublisher.uploads == 1
    assert FakePublisher.appended[0]["type"] == "file"
    assert FakePublisher.appended[0]["file"]["file_upload"] == {
        "id": "evaluation-html-upload"
    }
