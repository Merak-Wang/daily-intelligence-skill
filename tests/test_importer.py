import json
from pathlib import Path

from daily_intelligence.config import load_config
from daily_intelligence.importer import import_legacy


def test_importer_marks_401_as_verification_required(tmp_path: Path):
    source = tmp_path / "legacy.json"
    source.write_text(
        json.dumps(
            [
                {
                    "source": "Reuters",
                    "source_url": "https://www.reuters.com/",
                    "http_status": 401,
                    "status": "no_items",
                    "items": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    output = import_legacy(source, load_config(), tmp_path / "data")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["sources"][0]["status"] == "verification_required"


def test_importer_marks_429_as_rate_limited(tmp_path: Path):
    source = tmp_path / "legacy-429.json"
    source.write_text(
        json.dumps(
            [
                {
                    "source": "Reuters",
                    "source_url": "https://www.reuters.com/",
                    "status": "failed",
                    "http_status": 429,
                    "items": [],
                }
            ]
        ),
        encoding="utf-8",
    )

    output = import_legacy(source, load_config(), tmp_path / "data")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["sources"][0]["status"] == "rate_limited"


def test_importer_preserves_partial_status(tmp_path: Path):
    source = tmp_path / "legacy-partial.json"
    source.write_text(
        json.dumps(
            [
                {
                    "source": "Unknown Feed",
                    "source_url": "https://example.com/",
                    "http_status": 200,
                    "status": "partial",
                    "items": [],
                }
            ]
        ),
        encoding="utf-8",
    )
    output = import_legacy(source, load_config(), tmp_path / "data")
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["sources"][0]["status"] == "partial"
