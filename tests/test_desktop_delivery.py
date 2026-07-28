import base64
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

from daily_intelligence.config import OutputConfig, load_config, validate_output_config
from daily_intelligence.local_output import write_local_outputs


def _report() -> dict:
    return {
        "schema_version": "2.0",
        "report_id": "daily-2026-07-25-morning-r1",
        "date": "2026-07-25",
        "edition": "morning",
        "revision": 1,
        "title": "每日情报晨报 — 2026年7月25日",
        "generated_at": "2026-07-25T06:11:02+08:00",
        "executive_summary": [],
        "sections": [
            {
                "id": "information.international",
                "module": "information",
                "title": "国际",
                "briefs": [
                    {
                        "title": "Public source headline",
                        "title_zh": "公开来源标题",
                        "tldr": "这是一条公开来源摘要。",
                        "importance": 80,
                        "source_rank": 1,
                        "primary_source": {
                            "id": "example",
                            "name": "Example",
                            "url": "https://news.example/",
                        },
                        "source_ref": {"url": "https://news.example/story"},
                        "image": {
                            "local_path": "media/images/aa/example.jpg",
                            "source_url": "https://news.example/example.jpg",
                            "content_type": "image/jpeg",
                            "caption": "Public image",
                            "credit": "Example",
                        },
                    }
                ],
            }
        ],
        "analyses": [],
        "pending_verifications": [],
    }


def _jpeg_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (64, 36), color=(35, 74, 112)).save(buffer, format="JPEG")
    return buffer.getvalue()


def test_repository_config_delivers_html_to_desktop_by_default():
    config = load_config()

    assert config.output.copy_html_to_desktop is True
    assert config.output.desktop_dir is None


def test_desktop_directory_override_must_be_absolute():
    with pytest.raises(ValueError, match="desktop_dir must be an absolute path"):
        validate_output_config(
            OutputConfig(copy_html_to_desktop=True, desktop_dir="relative/Desktop")
        )


def test_html_projection_is_atomically_delivered_to_configured_desktop(
    tmp_path: Path,
):
    data_dir = tmp_path / "data"
    desktop_dir = tmp_path / "Desktop"
    config = OutputConfig(
        formats=["html", "pdf"],
        pdf_engine="reportlab",
        copy_html_to_desktop=True,
        desktop_dir=str(desktop_dir.resolve()),
    )
    image_bytes = _jpeg_bytes()
    image_path = data_dir / "media" / "images" / "aa" / "example.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(image_bytes)

    outputs = write_local_outputs(_report(), data_dir, config)

    desktop_path = Path(outputs["desktop_html_path"])
    assert desktop_path == (
        desktop_dir / "daily-intelligence-2026-07-25-morning-r1.html"
    )
    assert desktop_path.exists()
    assert not desktop_path.with_suffix(".html.tmp").exists()
    html = desktop_path.read_text(encoding="utf-8")
    assert (data_dir / "reports" / "index.html").resolve().as_uri() in html
    assert (
        data_dir / "reports" / "2026-07-25" / "morning-r1.pdf"
    ).resolve().as_uri() in html
    embedded = f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode('ascii')}"
    assert embedded in html
    assert f'{data_dir.resolve().as_uri()}/media/images/aa/example.jpg' not in html
    assert "../../media/images/aa/example.jpg" not in html
    assert html.index('class="brief-heading"') < html.index("<figure>")
    reader = PdfReader(outputs["pdf_path"])
    assert sum(len(page.images) for page in reader.pages) >= 1


def test_edge_pdf_receives_embedded_images_instead_of_relative_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    data_dir = tmp_path / "data"
    image_path = data_dir / "media" / "images" / "aa" / "example.jpg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_jpeg_bytes())
    captured: dict[str, str] = {}

    def fake_edge_pdf(
        _html_path: Path,
        output_path: Path,
        *,
        html_document: str | None = None,
    ) -> None:
        captured["html"] = html_document or ""
        output_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    monkeypatch.setattr("daily_intelligence.local_output._edge_pdf", fake_edge_pdf)
    outputs = write_local_outputs(
        _report(),
        data_dir,
        OutputConfig(
            formats=["html", "pdf"],
            pdf_engine="edge",
            copy_html_to_desktop=False,
        ),
    )

    assert outputs["pdf_engine"] == "edge"
    assert "data:image/jpeg;base64," in captured["html"]
    assert "../../media/images/aa/example.jpg" not in captured["html"]


def test_desktop_delivery_failure_is_explicit_without_losing_local_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    config = OutputConfig(
        formats=["html"],
        copy_html_to_desktop=True,
        desktop_dir=str((tmp_path / "Desktop").resolve()),
    )
    monkeypatch.setattr(
        "daily_intelligence.local_output.write_desktop_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )

    outputs = write_local_outputs(_report(), tmp_path / "data", config)

    assert Path(outputs["html_path"]).exists()
    assert "PermissionError: denied" in outputs["desktop_html_error"]
    assert outputs["desktop_html_error"] in outputs["warnings"]
