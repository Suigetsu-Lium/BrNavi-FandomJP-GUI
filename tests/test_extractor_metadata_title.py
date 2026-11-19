from modules.extractor import (
    extract_article_text,
    extract_page_title_from_html,
    is_probable_non_level_page_title,
)


def test_saved_article_fragment_uses_downloader_title_metadata():
    html = (
        "<!-- BRNAVI_META_INFO: TITLE=\u6848\u5185\u66f8\u5eab \u03b7 | "
        "URL=https://backrooms.fandom.com/ja/wiki/example | CATEGORY= | LANGUAGE=JP -->"
        "<div class=\"mw-content-ltr mw-parser-output\">"
        "<h2>\u6982\u8981</h2><p>\u8a18\u4e8b\u672c\u6587</p></div>"
    )

    title = extract_page_title_from_html(html)

    assert title == "\u6848\u5185\u66f8\u5eab \u03b7"
    assert not is_probable_non_level_page_title(title)
    assert "\u8a18\u4e8b\u672c\u6587" in extract_article_text(html)
