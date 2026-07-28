import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_hermes_skill",
    ROOT / "scripts" / "build_hermes_skill.py",
)
assert SPEC and SPEC.loader
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_skill_metadata_matches_hermes_and_agent_skill_contract():
    metadata = BUILD.parse_frontmatter(ROOT / "SKILL.md")
    hermes = metadata["metadata"]["hermes"]
    body = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("\n---\n", 1)[1]

    assert metadata["name"] == "signaltrail"
    assert metadata["description"].startswith("Use when ")
    assert len(metadata["description"]) <= 1024
    assert metadata["version"] == "2.0.0"
    assert metadata["author"] == "Wang Mingfeng"
    assert metadata["license"] == "MIT"
    assert metadata["platforms"] == ["windows", "macos", "linux"]
    assert hermes["category"] == "research"
    assert hermes["requires_toolsets"] == ["terminal"]
    assert "version" not in hermes
    assert "author" not in hermes
    assert "platforms" not in hermes
    assert "required_environment_variables" in metadata
    assert len(body.splitlines()) < 220


def test_community_package_contains_runtime_and_excludes_repository_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    for relative in BUILD.PACKAGE_FILES:
        origin = ROOT / relative
        destination = source / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, destination)
    for directory in BUILD.PACKAGE_DIRECTORIES:
        origin = ROOT / directory
        if origin.exists():
            shutil.copytree(origin, source / directory)
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)

    output = tmp_path / "release" / "signaltrail"
    result = BUILD.build_package(source, output)

    assert result["status"] == "built"
    assert result["name"] == "signaltrail"
    assert (output / "SKILL.md").is_file()
    assert (output / "src" / "daily_intelligence" / "cli.py").is_file()
    assert (output / "configs" / "sources.yaml").is_file()
    assert (output / "schemas" / "report.schema.json").is_file()
    assert (output / "scripts" / "install.ps1").is_file()
    assert not (output / ".git").exists()
    assert not (output / "tests").exists()
    assert not (output / "examples").exists()
    assert not (output / "wiki").exists()
    assert not (output / "data").exists()


def test_community_package_rejects_secret_like_content(tmp_path):
    suspicious = tmp_path / "credential.txt"
    suspicious.write_text(
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n",
        encoding="utf-8",
    )

    with pytest.raises(BUILD.PackageError, match="Potential secret"):
        BUILD.inspect_package_file(Path("references/credential.txt"), suspicious)
