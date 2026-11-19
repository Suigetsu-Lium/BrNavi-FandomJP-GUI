from pathlib import Path
from unittest.mock import patch

from modules import image_downloader


def test_download_images_copies_assets_saved_with_offline_html(tmp_path):
    html_dir = tmp_path / "downloaded_fandom_html"
    assets_dir = html_dir / "Guide_eta_assets"
    output_dir = tmp_path / "downloaded_images"
    assets_dir.mkdir(parents=True)
    asset = assets_dir / "image_01.webp"
    asset.write_bytes(b"local-webp-image")
    html_file = html_dir / "Guide_eta.html"
    html_file.write_text(
        "<div class=\"mw-parser-output\"><img src=\"Guide_eta_assets/image_01.webp\"></div>",
        encoding="utf-8",
    )

    with (
        patch.object(image_downloader, "OUTPUT_IMG_DIR", str(output_dir)),
        patch.object(image_downloader.requests, "get", side_effect=AssertionError("network should not be used")),
    ):
        result = image_downloader.download_images(
            html_files=[html_file],
            return_details=True,
        )

    saved = output_dir / "Guide_eta - image1.webp"
    assert result == {"downloaded": 1, "skipped": 0, "failed": 0}
    assert saved.read_bytes() == b"local-webp-image"


def test_legacy_image_rename_plan_and_execution_are_url_scoped_and_non_destructive(tmp_path):
    from modules.image_downloader import plan_legacy_image_renames, rename_legacy_images, page_image_filename

    html_dir = tmp_path / "html"
    image_dir = tmp_path / "images"
    html_dir.mkdir()
    image_dir.mkdir()
    page_url = "https://backrooms.fandom.com/ja/wiki/Level_Fog_%CE%B7"
    (html_dir / "Level_Fog_η.html").write_text(
        f"<!-- BRNAVI_META_INFO: TITLE=Level Fog η | URL={page_url} -->"
        '<div class="mw-parser-output"><img src="https://static.wikia.nocookie.net/a/Fog.jpg/revision/latest"><img src="https://static.wikia.nocookie.net/a/Fog2.png/revision/latest"></div>',
        encoding="utf-8",
    )
    first = image_dir / "Level_Fog_η - image1.jpg"
    second = image_dir / "Level_Fog_η - image2.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    plan = plan_legacy_image_renames(html_dir, image_dir, meta_title_map={})
    assert len(plan["items"]) == 2
    assert plan["items"][0]["target"].name == page_image_filename(page_url, 1, ".jpg")
    assert first.exists() and second.exists()  # preview never changes files

    result = rename_legacy_images(html_dir, image_dir, meta_title_map={})
    assert result["renamed"] == 2
    assert not first.exists() and not second.exists()
    assert (image_dir / page_image_filename(page_url, 1, ".jpg")).read_bytes() == b"first"
    assert (image_dir / page_image_filename(page_url, 2, ".png")).read_bytes() == b"second"


def test_legacy_image_rename_skips_ambiguous_and_existing_targets(tmp_path):
    from modules.image_downloader import plan_legacy_image_renames, page_image_filename

    html_dir = tmp_path / "html"
    image_dir = tmp_path / "images"
    html_dir.mkdir()
    image_dir.mkdir()
    page_url = "https://backrooms.fandom.com/ja/wiki/Level_Fog_%CE%B7"
    (html_dir / "Level_Fog_η.html").write_text(
        f"<!-- BRNAVI_META_INFO: TITLE=Level Fog η | URL={page_url} -->"
        '<div class="mw-parser-output"><img src="https://static.wikia.nocookie.net/a/Fog.jpg/revision/latest"></div>',
        encoding="utf-8",
    )
    legacy = image_dir / "Level_Fog_η - image1.jpg"
    legacy.write_bytes(b"keep")
    target = image_dir / page_image_filename(page_url, 1, ".jpg")
    target.write_bytes(b"existing")

    plan = plan_legacy_image_renames(html_dir, image_dir, meta_title_map={})
    assert plan["items"] == []
    assert legacy.read_bytes() == b"keep"
    assert target.read_bytes() == b"existing"
