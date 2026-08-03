from concurrent.futures import ThreadPoolExecutor

import pytest

from daily_intelligence.storage import write_immutable_json
from daily_intelligence.utils import (
    read_json,
    read_json_object,
    write_bytes_atomic,
    write_json,
    write_text_atomic,
)


def test_atomic_writers_share_one_collision_safe_implementation(tmp_path):
    text_path = tmp_path / "nested" / "record.txt"
    bytes_path = tmp_path / "record.bin"

    assert write_text_atomic(text_path, "readable") == text_path
    assert write_bytes_atomic(bytes_path, b"\x00\x01") == bytes_path
    assert text_path.read_text(encoding="utf-8") == "readable"
    assert bytes_path.read_bytes() == b"\x00\x01"
    assert not list(tmp_path.rglob("*.tmp"))


def test_concurrent_json_writes_never_share_a_temporary_file(tmp_path):
    path = tmp_path / "state.json"
    payloads = [{"writer": writer, "value": "x" * 1_000} for writer in range(8)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda payload: write_json(path, payload), payloads))

    assert read_json(path) in payloads
    assert not list(tmp_path.glob("*.tmp"))


def test_read_json_object_rejects_a_list_with_an_actionable_path(tmp_path):
    path = write_json(tmp_path / "items.json", [])

    with pytest.raises(ValueError, match=r"Run manifest must be a JSON object: .*items.json"):
        read_json_object(path, "Run manifest")


def test_immutable_json_has_one_winner_under_concurrent_creation(tmp_path):
    path = tmp_path / "reports" / "morning-r1.json"
    payloads = [{"writer": writer} for writer in range(8)]

    def attempt(payload):
        try:
            write_immutable_json(path, payload)
            return "created"
        except FileExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(attempt, payloads))

    assert outcomes.count("created") == 1
    assert outcomes.count("exists") == 7
    assert read_json(path) in payloads
    assert not list(path.parent.glob("*.tmp"))
