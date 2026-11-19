from modules.extractor import extract_article_attribution, extract_article_text


def test_extracts_author_credit_separately_from_article_body():
    html = """
    <div class="mw-parser-output">
      <p><small>この記事は <a href="/wiki/User:JapaneseNomad">JapaneseNomad</a> が 2022 年に作成したものです。</small></p>
      <p class="mw-customtoggle-voting"><small>(+/-) vote</small></p>
      <p>Level 20 η は無人駅です。</p>
      <h2><span class="mw-headline">概要</span></h2>
      <p>駅の外は危険です。</p>
    </div>
    """
    credit = extract_article_attribution(html)
    body = extract_article_text(html)

    assert "JapaneseNomad" in credit
    assert "2022" in credit
    assert "JapaneseNomad" not in body
    assert "(+/-) vote" not in body
    assert "Level 20 η は無人駅です。" in body
    assert "[概要]" in body


def test_removes_mobile_fallback_notice_but_keeps_real_article_content():
    html = """
    <div class="mw-parser-output">
      <p>読み込み中……もし、いつまで経っても、この表示から変わらないのなら、
      ページの一番下までスクロールして、そこにある "PC版で表示" をクリックして
      デスクトップ表示へ切り替えてみてください。</p>
      <p>これは記事の本文です。</p>
    </div>
    """
    body = extract_article_text(html)
    assert "読み込み中" not in body
    assert "PC版で表示" not in body
    assert "デスクトップ表示" not in body
    assert "これは記事の本文です。" in body


def test_internal_link_fragment_is_preserved():
    html = """
    <div class="mw-parser-output">
      <p><a href="/ja/wiki/Level_20_%CE%B7#destinations">行先</a></p>
    </div>
    """
    body = extract_article_text(html)
    assert "#destinations" in body
