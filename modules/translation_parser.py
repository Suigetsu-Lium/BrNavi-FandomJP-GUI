"""
Backrooms Navigation System - Fandom JP
Translation Index Parser

translation.html を解析し、

    見出し
      ↓
    その見出しに属するWikiリンク

という構造を保存する。

除外:
    ・?action=...
    ・?oldid=...
    ・その他クエリ付きURL
    ・特殊名前空間
"""

import os
from urllib.parse import urljoin, urldefrag, urlparse

from bs4 import BeautifulSoup


# =============================================================================
# パス
# =============================================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

INPUT_HTML = os.path.join(
    BASE_DIR,
    "index_html",
    "translation.html"
)

OUTPUT_DIR = os.path.join(
    BASE_DIR,
    "parsed_index"
)

OUTPUT_TXT = os.path.join(
    OUTPUT_DIR,
    "translation_structure.txt"
)

BASE_URL = "https://backrooms.fandom.com"

WIKI_PREFIX = (
    f"{BASE_URL}/ja/wiki/"
)


# =============================================================================
# 特殊名前空間
# =============================================================================

EXCLUDED_URL_PREFIXES = (
    "/ja/wiki/カテゴリ:",
    "/ja/wiki/Category:",
    "/ja/wiki/Template:",
    "/ja/wiki/テンプレート:",
    "/ja/wiki/File:",
    "/ja/wiki/ファイル:",
    "/ja/wiki/Help:",
    "/ja/wiki/ヘルプ:",
    "/ja/wiki/User:",
    "/ja/wiki/ユーザー:",
    "/ja/wiki/利用者:",
    "/ja/wiki/Talk:",
    "/ja/wiki/トーク:",
)


# =============================================================================
# Wikiリンク判定
# =============================================================================

def is_wiki_link(href):
    """
    Fandom JPのWikiページリンクか判定する。
    """

    if not href:
        return False

    url = urljoin(
        BASE_URL,
        href
    )

    if not url.startswith(
        WIKI_PREFIX
    ):
        return False

    return True


# =============================================================================
# URL正規化
# =============================================================================

def normalize_url(href):
    """
    URLを正規化する。

    戻り値:
        有効な記事URL
        または None
    """

    if not href:
        return None

    href = href.strip()

    if not href:
        return None

    # JavaScript
    if href.startswith(
        "javascript:"
    ):
        return None

    # mailto
    if href.startswith(
        "mailto:"
    ):
        return None

    # ページ内アンカーのみ
    if href.startswith(
        "#"
    ):
        return None

    url = urljoin(
        BASE_URL,
        href
    )

    # fragment除去
    url, _ = urldefrag(
        url
    )

    # -------------------------------------------------------------------------
    # クエリパラメータ除外
    # -------------------------------------------------------------------------

    parsed = urlparse(
        url
    )

    if parsed.query:
        return None

    # -------------------------------------------------------------------------
    # Wikiページ確認
    # -------------------------------------------------------------------------

    if not url.startswith(
        WIKI_PREFIX
    ):
        return None

    # -------------------------------------------------------------------------
    # 特殊名前空間除外
    # -------------------------------------------------------------------------

    for prefix in EXCLUDED_URL_PREFIXES:

        if url.startswith(
            f"{BASE_URL}{prefix}"
        ):
            return None

    return url


# =============================================================================
# 本文領域
# =============================================================================

def find_content_root(soup):

    selectors = [
        "#mw-content-text",
        ".mw-parser-output",
        ".page-content",
        ".WikiaArticle",
        ".article-content",
        "main",
    ]

    for selector in selectors:

        element = soup.select_one(
            selector
        )

        if element is not None:
            return element

    return soup


# =============================================================================
# リンク抽出
# =============================================================================

def extract_links(
    content
):

    links = []
    seen = set()

    for a in content.find_all(
        "a",
        href=True
    ):

        href = a.get(
            "href"
        )

        # -------------------------------------------------------------
        # URL
        # -------------------------------------------------------------

        url = normalize_url(
            href
        )

        if not url:
            continue

        # -------------------------------------------------------------
        # タイトル
        # -------------------------------------------------------------

        title = a.get_text(
            " ",
            strip=True
        )

        if not title:
            continue

        # -------------------------------------------------------------
        # URL重複
        # -------------------------------------------------------------

        if url in seen:
            continue

        seen.add(
            url
        )

        links.append({
            "title": title,
            "url": url,
        })

    return links


# =============================================================================
# 解析
# =============================================================================

def parse_translation_html(
    html
):

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    content = find_content_root(
        soup
    )

    sections = []

    current_heading = "（先頭）"
    current_links = []

    # -------------------------------------------------------------------------
    # 見出し・リンクの順序を維持
    # -------------------------------------------------------------------------

    for element in content.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "p",
            "ul",
            "ol",
            "table",
        ]
    ):

        # -------------------------------------------------------------
        # 見出し
        # -------------------------------------------------------------

        if element.name in {
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:

            if current_links:

                sections.append({
                    "heading": current_heading,
                    "links": current_links,
                })

            current_heading = element.get_text(
                " ",
                strip=True
            )

            current_links = []

            continue

        # -------------------------------------------------------------
        # リンク
        # -------------------------------------------------------------

        extracted = extract_links(
            element
        )

        for item in extracted:

            if item["url"] in {
                x["url"]
                for x in current_links
            }:
                continue

            current_links.append(
                item
            )

    # -------------------------------------------------------------------------
    # 最終セクション
    # -------------------------------------------------------------------------

    if current_links:

        sections.append({
            "heading": current_heading,
            "links": current_links,
        })

    return sections


# =============================================================================
# TXT保存
# =============================================================================

def save_structure(
    sections
):

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    total_links = 0

    with open(
        OUTPUT_TXT,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 80
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - Translation Index Structure\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )

        for section_index, section in enumerate(
            sections,
            start=1
        ):

            heading = section[
                "heading"
            ]

            links = section[
                "links"
            ]

            total_links += len(
                links
            )

            f.write(
                f"[SECTION {section_index:04d}]\n"
            )

            f.write(
                f"見出し: {heading}\n"
            )

            f.write(
                f"リンク数: {len(links)}\n"
            )

            f.write(
                "-" * 80
                + "\n"
            )

            for i, item in enumerate(
                links,
                start=1
            ):

                f.write(
                    f"[{i:04d}] "
                    f"{item['title']}\n"
                )

                f.write(
                    f"      {item['url']}\n"
                )

            f.write(
                "\n"
            )

    return total_links


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print("=" * 80)
    print(
        " Backrooms Fandom JP - Translation Index Parser"
    )
    print("=" * 80)

    if not os.path.exists(
        INPUT_HTML
    ):

        print()
        print(
            "【エラー】translation.html がありません。"
        )

        print(
            INPUT_HTML
        )

        return

    with open(
        INPUT_HTML,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        html = f.read()

    print()
    print(
        f"読み込みファイル:"
    )

    print(
        f"  {INPUT_HTML}"
    )

    print(
        f"HTMLサイズ: "
        f"{len(html):,} bytes"
    )

    # -------------------------------------------------------------------------
    # 解析
    # -------------------------------------------------------------------------

    sections = parse_translation_html(
        html
    )

    # -------------------------------------------------------------------------
    # 保存
    # -------------------------------------------------------------------------

    total_links = save_structure(
        sections
    )

    print()
    print("=" * 80)

    print(
        "Translation Index 解析完了"
    )

    print(
        "=" * 80
    )

    print(
        f"セクション数: "
        f"{len(sections)}"
    )

    print(
        f"抽出リンク総数: "
        f"{total_links}"
    )

    print(
        f"保存先: "
        f"{OUTPUT_TXT}"
    )


if __name__ == "__main__":
    main()