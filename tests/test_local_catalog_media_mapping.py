from pathlib import Path
from tempfile import TemporaryDirectory

from modules.system_dashboard import find_local_images, list_local_images, local_saved_level_list


def test_gallery_recognizes_nonnumeric_eta_and_non_level_identifiers():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "Level_Fog_η - image1.jpg").write_bytes(b"x")
        (root / "素数の森林_η - image1.jpg").write_bytes(b"x")
        images = list_local_images(root)
    assert [item["level_id"] for item in images] == ["Level Fog η", "素数の森林 η"]


def test_saved_level_list_includes_nonnumeric_eta_pages_and_deduplicates():
    candidates = [
        {"title": "Level 2", "saved": True},
        {"title": "Level Fog η", "saved": True},
        {"title": "The Err0r η", "saved": True},
        {"title": "素数の森林 η", "saved": True},
        {"title": "The Hub", "saved": True},
        {"title": "Level 3", "saved": False},
        {"title": "Level Fog η", "saved": True},
    ]
    result = local_saved_level_list(candidates)
    assert [item["title"] for item in result] == [
        "Level 2", "Level Fog \u03b7", "The Err0r \u03b7", "\u7d20\u6570\u306e\u68ee\u6797 \u03b7", "The Hub"
    ]

def test_article_image_lookup_matches_spaces_underscores_japanese_and_translation_prefixes():
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "Level_Fog_\u03b7 - image1.jpg").write_bytes(b"a")
        (root / "\u968f\u7b46\u8ff7\u5bae_\u03b7 - image1.jpg").write_bytes(b"b")
        (root / "\u7ffb\u8a33_Level_37_\u7ffb\u8a33_Level_37_(1)_Sublimity - image1.gif").write_bytes(b"c")
        assert [p.name for p in find_local_images(root, "Level Fog \u03b7")] == ["Level_Fog_\u03b7 - image1.jpg"]
        assert [p.name for p in find_local_images(root, "\u968f\u7b46\u8ff7\u5bae \u03b7")] == ["\u968f\u7b46\u8ff7\u5bae_\u03b7 - image1.jpg"]
        assert [p.name for p in find_local_images(root, "\u7ffb\u8a33/Level 37")] == ["\u7ffb\u8a33_Level_37_\u7ffb\u8a33_Level_37_(1)_Sublimity - image1.gif"]


def test_gallery_maps_url_keyed_images_back_to_distinct_catalog_pages():
    import hashlib
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        first_url = "https://backrooms.fandom.com/ja/wiki/Almond_Water"
        second_url = "https://backrooms.fandom.com/ja/wiki/%E7%BF%BB%E8%A8%B3/Almond_Water"
        first_key = hashlib.sha256(first_url.encode("utf-8")).hexdigest()[:16]
        second_key = hashlib.sha256(second_url.encode("utf-8")).hexdigest()[:16]
        (root / f"page_{first_key}_image1.jpg").write_bytes(b"original")
        (root / f"page_{second_key}_image1.jpg").write_bytes(b"translated")
        pages = [
            {"title": "Almond Water", "url": first_url},
            {"title": "翻訳/Almond Water", "url": second_url},
        ]
        images = list_local_images(root, known_pages=pages)
    assert {item["level_id"] for item in images} == {"Almond Water", "翻訳/Almond Water"}
