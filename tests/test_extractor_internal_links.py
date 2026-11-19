from modules.extractor import extract_article_text, process_html_file


def test_extract_article_text_preserves_fandom_internal_link_target():
    html = """
    <html><body><div class="mw-parser-output">
      <p>Go to <a href="/ja/wiki/Level_37">Level 37</a>.</p>
    </div></body></html>
    """
    text = extract_article_text(html)
    assert "[[https://backrooms.fandom.com/ja/wiki/Level_37|Level 37]]" in text


def test_process_html_file_extracts_translation_page_without_level_code(tmp_path):
    html_path = tmp_path / "translation_test.html"
    output_dir = tmp_path / "texts"
    html_path.write_text(
        """<!-- BRNAVI_META_INFO: TITLE=Translation/Test Article|URL=https://backrooms.fandom.com/ja/wiki/Translation/Test_Article|CATEGORY=normal|LANGUAGE=EN|SOURCE=translation|INDEXES=translation -->
        <html><head><title>Translation/Test Article - Backrooms Wiki</title></head>
        <body><div class="mw-parser-output">
          <p>This is a translated article with enough meaningful content to keep.</p>
          <p>See <a href="/ja/wiki/Level_12">Level 12</a> for another location.</p>
        </div></body></html>""",
        encoding="utf-8",
    )
    row = process_html_file(html_path, output_dir, 1)
    assert row is not None
    assert "translation" in row.values()
    assert "Translation/Test Article" in row.values()
    saved_files = list(output_dir.glob("*.txt"))
    assert len(saved_files) == 1
    saved = saved_files[0].read_text(encoding="utf-8-sig")
    assert "translated article" in saved
    assert "[[https://backrooms.fandom.com/ja/wiki/Level_12|Level 12]]" in saved
