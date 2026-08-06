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
from daily_intelligence.runtime import (
    bind_data_root,
    require_data_root_path,
    validate_run_data_root,
)


def test_windows_defaults_to_edge_without_overrides(monkeypatch):
    monkeypatch.delenv("DAILY_INTEL_BROWSER_CHANNEL", raising=False)
    config = AppConfig(timezone="Asia/Shanghai", browser=BrowserConfig(), sources=[])

    assert resolve_browser_channel(config, platform="nt") == "msedge"
    assert resolve_browser_channel(config, platform="posix") is None


def test_local_html_and_pdf_are_default_reading_outputs():
    config = load_config()

    assert config.collection.item_order == "source"
    assert all(source.item_order == "source" for source in config.all_monitor_sources)
    assert config.budget.max_runtime_seconds == 3600
    assert config.output.language == "zh-CN"
    assert config.output.formats == ["html", "pdf"]
    assert config.output.pdf_engine == "edge"
    assert config.output.open_after_finalize is False
    assert config.output.copy_html_to_desktop is True
    assert config.output.desktop_dir is None
    assert config.media.enabled is True
    assert config.media.max_images_per_report == 1000
    assert config.media.max_image_bytes == 8 * 1024 * 1024
    assert config.media.global_concurrency == 12
    assert config.media.per_domain_concurrency == 2
    assert config.media.cache_success_ttl_hours == 168


def test_existing_config_without_desktop_key_inherits_desktop_delivery(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "timezone: Asia/Shanghai\noutput:\n  formats: [html]\nsources: []\n",
        encoding="utf-8",
    )

    assert load_config(config_path).output.copy_html_to_desktop is True


def test_collection_item_order_supports_global_default_and_source_override(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "timezone: Asia/Shanghai\n"
        "collection:\n  item_order: published_at\n"
        "sources:\n"
        "- id: inherited\n"
        "  name: Inherited\n"
        "  url: https://example.com/news\n"
        "- id: overridden\n"
        "  name: Overridden\n"
        "  url: https://example.org/news\n"
        "  item_order: source\n",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.source_by_id("inherited").item_order == "published_at"
    assert config.source_by_id("overridden").item_order == "source"


def test_collection_item_order_rejects_unknown_values(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "timezone: Asia/Shanghai\n"
        "collection:\n  item_order: popularity\n"
        "sources: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="collection.item_order"):
        load_config(config_path)


def test_monitor_expands_sources_without_changing_newspaper_quotas():
    config = load_config()

    assert len(config.sources) == 32
    assert all(source.report_target == 15 for source in config.sources)
    assert all(source.report_max == 15 for source in config.sources)
    assert len(config.monitor_sources) == 51
    assert len(config.all_monitor_sources) == 83
    assert all(source.report_target == 0 for source in config.monitor_sources)
    assert config.budget.max_agent_tokens == 10_000_000
    assert config.budget.max_fulltext_per_run == 12
    assert config.monitor.default_refresh_interval_minutes == 30
    assert config.monitor.max_items_per_feed == 40
    assert config.monitor.reuse_fresh_snapshot_before_edition is True


def test_pdf_output_requires_html_and_known_engine(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "timezone: Asia/Shanghai\noutput:\n  formats: [pdf]\nsources: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="PDF output requires HTML"):
        load_config(config_path)

    config_path.write_text(
        "timezone: Asia/Shanghai\n"
        "output:\n  formats: [html, pdf]\n  pdf_engine: unknown\n"
        "sources: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pdf_engine"):
        load_config(config_path)


def test_output_language_is_limited_to_chinese_or_english(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "timezone: Asia/Shanghai\n"
        "output:\n  language: en\n  formats: [html]\n"
        "sources: []\n",
        encoding="utf-8",
    )

    assert load_config(config_path).output.language == "en"

    config_path.write_text(
        "timezone: Asia/Shanghai\n"
        "output:\n  language: fr\n  formats: [html]\n"
        "sources: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="output.language"):
        load_config(config_path)


def test_media_limits_must_fit_notion_direct_upload(tmp_path):
    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "timezone: Asia/Shanghai\n"
        "media:\n"
        "  max_image_bytes: 20971521\n"
        "  max_total_bytes: 41943042\n"
        "sources: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="20 MB"):
        load_config(config_path)


def test_project_root_uses_explicit_stable_skill_directory(monkeypatch, tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "schemas").mkdir()
    (tmp_path / "SKILL.md").write_text("---\nname: signaltrail\n---\n", encoding="utf-8")
    (tmp_path / "configs" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    (tmp_path / "schemas" / "report.schema.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("DAILY_INTEL_SKILL_DIR", str(tmp_path))

    assert project_root() == tmp_path.resolve()


def test_project_root_discovers_new_hermes_path_before_legacy_path(
    monkeypatch,
    tmp_path,
):
    import daily_intelligence.config as config_module

    hermes_home = tmp_path / "hermes"
    new_skill = hermes_home / "skills" / "research" / "signaltrail"
    legacy_brand_skill = hermes_home / "skills" / "research" / "merak-brief"
    legacy_skill = hermes_home / "skills" / "research" / "daily-intelligence"
    for skill_path in (new_skill, legacy_brand_skill, legacy_skill):
        (skill_path / "configs").mkdir(parents=True)
        (skill_path / "schemas").mkdir()
        (skill_path / "SKILL.md").write_text("---\nname: signaltrail\n---\n", encoding="utf-8")
        (skill_path / "configs" / "sources.yaml").write_text(
            "sources: []\n",
            encoding="utf-8",
        )
        (skill_path / "schemas" / "report.schema.json").write_text(
            "{}\n",
            encoding="utf-8",
        )
    fake_module = tmp_path / "site-packages" / "daily_intelligence" / "config.py"
    fake_module.parent.mkdir(parents=True)
    fake_module.write_text("", encoding="utf-8")
    empty_cwd = tmp_path / "cwd"
    empty_cwd.mkdir()
    monkeypatch.setattr(config_module, "__file__", str(fake_module))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.delenv("DAILY_INTEL_SKILL_DIR", raising=False)
    monkeypatch.chdir(empty_cwd)

    assert project_root() == new_skill.resolve()


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

    assert resolve_data_dir() == (hermes_home / "daily-intelligence").resolve()
    assert (
        resolve_profile_dir(config)
        == (hermes_home / "browser-profiles" / "daily-intelligence").resolve()
    )


def test_explicit_data_root_cannot_override_environment(monkeypatch, tmp_path):
    configured = tmp_path / "configured"
    monkeypatch.setenv("DAILY_INTEL_DATA_DIR", str(configured))

    with pytest.raises(ValueError, match="conflicts with DAILY_INTEL_DATA_DIR"):
        resolve_data_dir(tmp_path / "other")

    assert resolve_data_dir(configured) == configured.resolve()


def test_live_hermes_data_root_is_bound_once(tmp_path):
    hermes_home = tmp_path / "hermes"
    first = hermes_home / "daily-intelligence"
    second = hermes_home / "daily-intel-data"

    result = bind_data_root(first, hermes_home)

    assert result["status"] == "bound"
    with pytest.raises(ValueError, match="already bound"):
        bind_data_root(second, hermes_home)
    adopted = bind_data_root(second, hermes_home, adopt=True)
    assert adopted["status"] == "adopted"
    assert adopted["previous_data_root"] == str(first.resolve())


def test_run_artifacts_must_remain_under_active_data_root(tmp_path):
    data_dir = tmp_path / "canonical"
    run_path = data_dir / "runs" / "2026-07-17" / "morning.json"
    run = {
        "data_root": str(data_dir.resolve()),
        "artifacts": {"index_path": str(tmp_path / "other" / "index.json")},
    }

    with pytest.raises(ValueError, match="outside the active DAILY_INTEL_DATA_DIR"):
        validate_run_data_root(run, run_path, data_dir)
    with pytest.raises(ValueError, match="outside the active DAILY_INTEL_DATA_DIR"):
        require_data_root_path(tmp_path / "draft.json", data_dir, "test artifact")


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


def test_hugging_face_uses_top_page_and_rewrites_legacy_queue_urls():
    config = load_config()

    expected = "https://huggingface.co/papers/trending"
    assert config.source_by_id("huggingface_papers").url == expected
    assert canonical_source_page_url(
        "huggingface_papers", "https://huggingface.co/papers/month"
    ) == expected
    assert canonical_source_page_url(
        "huggingface_papers", "https://huggingface.co/papers"
    ) == expected
