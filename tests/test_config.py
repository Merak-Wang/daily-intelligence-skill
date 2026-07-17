import pytest

from daily_intelligence.cli import load_hermes_environment
from daily_intelligence.config import (
    AppConfig,
    BrowserConfig,
    add_source_page,
    canonical_source_page_url,
    load_config,
    load_source_pages,
    project_root,
    resolve_browser_channel,
    resolve_data_dir,
    resolve_hermes_home,
    resolve_profile_dir,
    source_urls,
)


def test_windows_defaults_to_edge_without_overrides(monkeypatch):
    monkeypatch.delenv("DAILY_INTEL_BROWSER_CHANNEL", raising=False)
    config = AppConfig(timezone="Asia/Shanghai", browser=BrowserConfig(), sources=[])

    assert resolve_browser_channel(config, platform="nt") == "msedge"
    assert resolve_browser_channel(config, platform="posix") is None


def test_project_root_uses_explicit_stable_skill_directory(monkeypatch, tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "SKILL.md").write_text("---\nname: daily-intelligence\n---\n", encoding="utf-8")
    (tmp_path / "configs" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (tmp_path / "schemas" / "report.schema.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("DAILY_INTEL_SKILL_DIR", str(tmp_path))

    assert project_root() == tmp_path.resolve()


def test_explicit_browser_channel_overrides_windows_default(monkeypatch):
    monkeypatch.setenv("DAILY_INTEL_BROWSER_CHANNEL", "chromium")
    config = AppConfig(timezone="Asia/Shanghai", browser=BrowserConfig(), sources=[])

    assert resolve_browser_channel(config, "msedge", platform="nt") == "msedge"


def test_windows_hermes_home_uses_local_app_data(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert resolve_hermes_home(platform="nt") == (tmp_path / "hermes").resolve()


def test_hermes_home_override_controls_runtime_defaults(monkeypatch, tmp_path):
    hermes_home = tmp_path / "custom-hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("DAILY_INTEL_DATA_DIR", raising=False)
    monkeypatch.delenv("DAILY_INTEL_PROFILE_DIR", raising=False)
    config = AppConfig(timezone="Asia/Shanghai", browser=BrowserConfig(), sources=[])

    assert resolve_data_dir() == (hermes_home / "data" / "daily-intelligence").resolve()
    assert (
        resolve_profile_dir(config)
        == (hermes_home / "browser-profiles" / "daily-intelligence").resolve()
    )


def test_cli_loads_active_hermes_env_without_overriding(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(
        "daily_intelligence.cli.load_dotenv",
        lambda path, override: calls.append((path, override)),
    )

    env_path = load_hermes_environment()

    assert env_path == tmp_path / ".env"
    assert calls == [(tmp_path / ".env", False)]


def test_dynamic_source_pages_are_domain_scoped_and_persistent(tmp_path):
    config = load_config()
    url = "https://www.bbc.com/news/uk"

    path = add_source_page(config, tmp_path, "bbc_world", url, "英国新闻相关")

    assert path.exists()
    assert load_source_pages(tmp_path)[0]["reason"] == "英国新闻相关"
    assert url in source_urls(config.source_by_id("bbc_world"), tmp_path)
    with pytest.raises(ValueError, match="outside configured domains"):
        add_source_page(config, tmp_path, "bbc_world", "https://example.com/news", "bad")


def test_guardian_and_bbc_use_relevant_multi_page_sources():
    config = load_config()
    guardian = config.source_by_id("guardian_uk")
    bbc = config.source_by_id("bbc_world")

    assert guardian.region == "uk"
    assert "theguardian.com" in guardian.url
    assert any("business" in url for url in guardian.explore_urls)
    assert any("technology" in url for url in bbc.explore_urls)
    with pytest.raises(KeyError):
        config.source_by_id("guardian_ng")


def test_hugging_face_uses_stable_papers_page_and_rewrites_legacy_queue_url():
    config = load_config()

    assert config.source_by_id("huggingface_papers").url == "https://huggingface.co/papers"
    assert (
        canonical_source_page_url(
            "huggingface_papers", "https://huggingface.co/papers/month"
        )
        == "https://huggingface.co/papers"
    )
