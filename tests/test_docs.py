import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_docs",
    ROOT / "scripts" / "check_docs.py",
)
assert SPEC and SPEC.loader
DOCS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOCS)


def test_repository_documentation_map_is_complete_and_linked():
    assert DOCS.validate_docs() == []


def test_agent_map_stays_small_and_every_record_has_a_translation():
    assert len((ROOT / "AGENTS.md").read_text(encoding="utf-8").splitlines()) <= 110
    assert all(
        DOCS.translation_path(record).is_file()
        for record in DOCS.canonical_records()
    )
