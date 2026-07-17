import json
from pathlib import Path

import pytest

from daily_intelligence.config import load_config
from daily_intelligence.notion import publish_report


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

        def append_blocks(self, _page_id, blocks, start_block=0, on_progress=None):
            starts.append(start_block)
            if FakePublisher.attempts == 0:
                FakePublisher.attempts += 1
                on_progress(100)
                raise RuntimeError("simulated second-batch failure")
            on_progress(len(blocks))
            return len(blocks)

    monkeypatch.setenv("NOTION_TOKEN", "test-token")
    monkeypatch.setenv("NOTION_DATA_SOURCE_ID", "test-source")
    monkeypatch.setattr("daily_intelligence.notion.NotionPublisher", FakePublisher)
    monkeypatch.setattr(
        "daily_intelligence.notion.report_to_blocks",
        lambda _report: [{"object": "block"}] * 150,
    )

    with pytest.raises(RuntimeError, match="second-batch"):
        publish_report(report_path, tmp_path)

    registry_path = tmp_path / "publishing" / "notion-registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entry = registry[registry_key]
    assert entry["status"] == "publishing"
    assert entry["blocks_appended"] == 100

    page_id, status = publish_report(report_path, tmp_path)

    assert (page_id, status) == ("page-1", "published")
    assert starts == [0, 100]
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry[registry_key]["status"] == "complete"
