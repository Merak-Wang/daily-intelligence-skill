import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_code_comments",
    ROOT / "scripts" / "check_code_comments.py",
)
assert SPEC and SPEC.loader
CODE_COMMENTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CODE_COMMENTS)


def test_maintained_python_definitions_have_semantic_chinese_contracts():
    assert CODE_COMMENTS.validate_code_comments() == []
