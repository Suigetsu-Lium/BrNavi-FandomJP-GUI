"""
Backrooms Navigation System - Fandom JP
Index Parser Module

index_html/ 内に保存されたFandom JPの各種一覧HTMLを解析し、

    1. 一覧ページ内のWikiリンクを抽出
    2. 特殊URL・操作URLを除外
    3. all_links.csv を生成
    4. 一覧・案内ページを除外
    5. 同一URLを統合
    6. pages_database.csv を生成
    7. 確認用TXTを生成

さらに一覧ページ側に存在するLevel表記から、

    title
    meta_title
    meta_title_ja

を抽出する。

例:

    Level 1 (1): "The Habitable Zone" (生存可能領域)

        title          = Level 1
        meta_title     = The Habitable Zone
        meta_title_ja  = 生存可能領域


    Level 1 η: "The Liminalrooms" (ザ・リミナルルームズ)

        title          = Level 1 η
        meta_title     = The Liminalrooms
        meta_title_ja  = ザ・リミナルルームズ


    Level 2 η: "工場地帯"

        title          = Level 2 η
        meta_title     = 工場地帯
        meta_title_ja  =


重要:
    個別Level HTMLからメタタイトルを推測しない。

    一覧ページに記載されている情報を正本として扱う。

旧仕様:
    wiki_list.txt は使用しない。
"""


import csv
import os
import re

from urllib.parse import (
    urljoin,
    urldefrag,
    urlparse,
)

from bs4 import BeautifulSoup


# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

INDEX_HTML_DIR = os.path.join(
    BASE_DIR,
    "index_html"
)

OUTPUT_DIR = os.path.join(
    BASE_DIR,
    "parsed_index"
)

ALL_LINKS_CSV = os.path.join(
    OUTPUT_DIR,
    "all_links.csv"
)

PAGES_DATABASE_CSV = os.path.join(
    OUTPUT_DIR,
    "pages_database.csv"
)


# =============================================================================
# Fandom基本URL
# =============================================================================

BASE_URL = (
    "https://backrooms.fandom.com"
)

WIKI_PREFIX = (
    f"{BASE_URL}/ja/wiki/"
)


# =============================================================================
# 対象一覧HTML
# =============================================================================

INDEX_FILES = {
    "hierarchy": "hierarchy.html",
    "translation": "translation.html",
    "normal_eta": "normal_eta.html",
    "anomalous": "anomalous.html",
    "sublevel": "sublevel.html",
}


# =============================================================================
# Fandom特殊名前空間
# =============================================================================

EXCLUDED_URL_PREFIXES = (

    "/ja/wiki/カテゴリ:",
    "/ja/wiki/Category:",

    "/ja/wiki/特別:",
    "/ja/wiki/Special:",

    "/ja/wiki/Template:",
    "/ja/wiki/テンプレート:",

    "/ja/wiki/ファイル:",
    "/ja/wiki/File:",

    "/ja/wiki/Help:",
    "/ja/wiki/ヘルプ:",

    "/ja/wiki/User:",
    "/ja/wiki/ユーザー:",
    "/ja/wiki/利用者:",

    "/ja/wiki/Talk:",
    "/ja/wiki/トーク:",

    "/ja/wiki/MediaWiki:",
    "/ja/wiki/メディアウィキ:",
)


# =============================================================================
# 明らかな一覧・案内ページ
# =============================================================================

EXCLUDED_EXACT_TITLES = {

    "The Backrooms",
    "階層",

    "翻訳の一覧",
    "通常階層 η の一覧",
    "番外階層の一覧",
    "亜階層の一覧",

    "無印通常階層の一覧",
    "無印番外階層の一覧",
}


# =============================================================================
# タイトルによる一覧ページ除外
# =============================================================================

EXCLUDED_TITLE_KEYWORDS = (
    "の一覧",
)


# =============================================================================
# 明らかなナビゲーションページ
# =============================================================================

EXCLUDED_NAVIGATION_TITLES = {
    "Backrooms Wiki",
    "カテゴリ",
    "一覧",
}


# =============================================================================
# 本文領域探索
# =============================================================================

def find_content_roots(soup):
    """
    Fandomページ本文領域の候補を探す。
    """

    selectors = [

        "#mw-content-text",

        ".mw-parser-output",

        ".page-content",

        ".WikiaArticle",

        ".article-content",

        "main",
    ]

    roots = []

    for selector in selectors:

        elements = soup.select(
            selector
        )

        for element in elements:

            if element not in roots:

                roots.append(
                    element
                )

    return roots


# =============================================================================
# 共通文字列処理
# =============================================================================

def clean_text(value):
    """
    HTML上の文字列を簡易正規化する。
    """

    if value is None:

        return ""

    value = (
        str(value)
        .replace(
            "\xa0",
            " "
        )
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def clean_meta_text(value):
    """
    メタタイトル候補を整える。
    """

    value = clean_text(
        value
    )

    if not value:

        return ""

    return value.strip(
        '「」『』"\''
    ).strip()


# =============================================================================
# 一覧項目からタイトル・メタタイトル・日本語名を解析
# =============================================================================

def parse_listing_text(
    text,
    fallback_title=""
):
    """
    一覧ページ上の1項目から、

        title
        meta_title
        meta_title_ja

    を抽出する。

    対応例:

        Level 1 (1): "The Habitable Zone" (生存可能領域)

        Level 1 η: "The Liminalrooms" (ザ・リミナルルームズ)

        Level 2 η: "工場地帯"

        Level 1

    Returns
    -------
    tuple[str, str, str]
        title, meta_title, meta_title_ja
    """

    text = clean_text(
        text
    )

    if not text:

        return (
            clean_text(
                fallback_title
            ),
            "",
            ""
        )

    # =========================================================================
    # 形式1
    #
    # Level 1 (1): "The Habitable Zone" (生存可能領域)
    #
    # Level 1 η: "The Liminalrooms" (ザ・リミナルルームズ)
    # =========================================================================

    match = re.match(
        r"^(.+?)"
        r"\s*(?:\(\d+\))?"
        r"\s*:\s*"
        r"\"([^\"]+)\""
        r"(?:\s*\(([^()]*)\))?"
        r"\s*$",
        text
    )

    if match:

        title = clean_text(
            match.group(1)
        )

        meta_title = clean_meta_text(
            match.group(2)
        )

        meta_title_ja = clean_meta_text(
            match.group(3)
            or ""
        )

        return (
            title,
            meta_title,
            meta_title_ja
        )

    # =========================================================================
    # 形式2
    #
    # Level 2 η: "工場地帯"
    # =========================================================================

    match = re.match(
        r"^(.+?)"
        r"\s*(?:\(\d+\))?"
        r"\s*:\s*"
        r"\"([^\"]+)\""
        r"\s*$",
        text
    )

    if match:

        title = clean_text(
            match.group(1)
        )

        meta_title = clean_meta_text(
            match.group(2)
        )

        return (
            title,
            meta_title,
            ""
        )

    # =========================================================================
    # 形式3
    #
    # Level 1 (1)
    #
    # 枝番だけ取り除く
    # =========================================================================

    match = re.match(
        r"^(.+?)\s*\(\d+\)\s*$",
        text
    )

    if match:

        title = clean_text(
            match.group(1)
        )

        return (
            title,
            "",
            ""
        )

    # =========================================================================
    # 形式4
    #
    # 引用符付き名称だけ存在するケース
    # =========================================================================

    match = re.match(
        r"^(.+?)\s*:\s*\"([^\"]+)\""
        r"(?:\s*\(([^()]*)\))?"
        r"\s*$",
        text
    )

    if match:

        title = clean_text(
            match.group(1)
        )

        meta_title = clean_meta_text(
            match.group(2)
        )

        meta_title_ja = clean_meta_text(
            match.group(3)
            or ""
        )

        return (
            title,
            meta_title,
            meta_title_ja
        )

    # =========================================================================
    # 形式5
    #
    # 解析できなかった場合
    # =========================================================================

    return (
        clean_text(
            fallback_title
            or text
        ),
        "",
        ""
    )


# =============================================================================
# アンカー周辺から一覧表記を取得
# =============================================================================

def get_listing_context(
    anchor
):
    """
    Levelリンクを含む一覧項目全体の文字列を取得する。

    優先:
        tr
        li
        親div
        親要素
        anchor自身
    """

    # =========================================================================
    # 1. table row
    # =========================================================================

    row = anchor.find_parent(
        "tr"
    )

    if row:

        text = clean_text(
            row.get_text(
                " ",
                strip=True
            )
        )

        if text:

            return text

    # =========================================================================
    # 2. li
    # =========================================================================

    list_item = anchor.find_parent(
        "li"
    )

    if list_item:

        text = clean_text(
            list_item.get_text(
                " ",
                strip=True
            )
        )

        if text:

            return text

    # =========================================================================
    # 3. 親要素を数段階確認
    # =========================================================================

    current = anchor

    for _ in range(
        3
    ):

        current = current.parent

        if not current:

            break

        text = clean_text(
            current.get_text(
                " ",
                strip=True
            )
        )

        if not text:

            continue

        # 長すぎる場合は一覧項目全体ではなく
        # ページ全体を拾ってしまう可能性がある
        if len(text) <= 300:

            return text

    # =========================================================================
    # 4. anchor
    # =========================================================================

    return clean_text(
        anchor.get_text(
            " ",
            strip=True
        )
    )


# =============================================================================
# リンク候補タイトル抽出
# =============================================================================

def extract_page_title_from_anchor(
    anchor
):
    """
    Anchor自身からページタイトル候補を取得する。
    """

    title = clean_text(
        anchor.get_text(
            " ",
            strip=True
        )
    )

    if not title:

        return ""

    return title


# =============================================================================
# URL正規化
# =============================================================================

def normalize_url(href):
    """
    相対URLを絶対URLへ変換し、
    #fragmentを除去する。

    除外対象・不正URLの場合はNone。
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

    # anchor
    if href.startswith(
        "#"
    ):

        return None

    # 相対URL
    url = urljoin(
        BASE_URL,
        href
    )

    # fragment除去
    url, _ = urldefrag(
        url
    )

    parsed = urlparse(
        url
    )

    # query付き除外
    if parsed.query:

        return None

    # Fandom JP
    if not url.startswith(
        WIKI_PREFIX
    ):

        return None

    # 特殊namespace
    for prefix in EXCLUDED_URL_PREFIXES:

        if parsed.path.startswith(
            prefix
        ):

            return None

    return url


# =============================================================================
# WikiページURL判定
# =============================================================================

def is_wiki_page(url):
    """
    Fandom JP WikiページURLか判定する。
    """

    if not url:

        return False

    return url.startswith(
        WIKI_PREFIX
    )


# =============================================================================
# URL除外判定
# =============================================================================

def is_excluded_url(url):
    """
    特殊URLを除外する。
    """

    if not url:

        return True

    parsed = urlparse(
        url
    )

    if parsed.query:

        return True

    if not parsed.path.startswith(
        "/ja/wiki/"
    ):

        return True

    for prefix in EXCLUDED_URL_PREFIXES:

        if parsed.path.startswith(
            prefix
        ):

            return True

    return False


# =============================================================================
# タイトル除外判定
# =============================================================================

def is_excluded_title(
    title
):
    """
    明らかな一覧・案内・ナビゲーションページを除外する。
    """

    if not title:

        return True

    title = title.strip()

    if title in EXCLUDED_EXACT_TITLES:

        return True

    if title in EXCLUDED_NAVIGATION_TITLES:

        return True

    for keyword in EXCLUDED_TITLE_KEYWORDS:

        if keyword in title:

            return True

    return False


# =============================================================================
# リンク抽出
# =============================================================================

def extract_links_from_html(
    html,
    index_name
):
    """
    一覧HTMLからWiki記事リンクを抽出する。

    Returns
    -------
    list[dict]

        {
            "title": str,
            "meta_title": str,
            "meta_title_ja": str,
            "url": str,
            "index_name": str
        }
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    # -------------------------------------------------------------------------
    # 本文領域
    # -------------------------------------------------------------------------

    roots = find_content_roots(
        soup
    )

    if not roots:

        roots = [
            soup
        ]

    results = []

    seen_urls = set()

    # -------------------------------------------------------------------------
    # リンク探索
    # -------------------------------------------------------------------------

    for root in roots:

        for anchor in root.find_all(
            "a",
            href=True
        ):

            href = anchor.get(
                "href"
            )

            # -----------------------------------------------------------------
            # URL
            # -----------------------------------------------------------------

            url = normalize_url(
                href
            )

            if not url:

                continue

            if not is_wiki_page(
                url
            ):

                continue

            if is_excluded_url(
                url
            ):

                continue

            # -----------------------------------------------------------------
            # Anchor title
            # -----------------------------------------------------------------

            anchor_title = (
                extract_page_title_from_anchor(
                    anchor
                )
            )

            if not anchor_title:

                continue

            # -----------------------------------------------------------------
            # 一覧コンテキスト
            # -----------------------------------------------------------------

            context = (
                get_listing_context(
                    anchor
                )
            )

            # -----------------------------------------------------------------
            # title / meta_title / meta_title_ja
            # -----------------------------------------------------------------

            (
                parsed_title,
                meta_title,
                meta_title_ja
            ) = parse_listing_text(
                context,
                anchor_title
            )

            # -----------------------------------------------------------------
            # titleのfallback
            # -----------------------------------------------------------------

            if not parsed_title:

                parsed_title = (
                    anchor_title
                )

            # -----------------------------------------------------------------
            # 除外
            # -----------------------------------------------------------------

            if is_excluded_title(
                parsed_title
            ):

                continue

            # -----------------------------------------------------------------
            # URL重複
            # -----------------------------------------------------------------

            if url in seen_urls:

                continue

            seen_urls.add(
                url
            )

            # -----------------------------------------------------------------
            # 結果
            # -----------------------------------------------------------------

            results.append({

                "title": parsed_title,

                "meta_title": meta_title,

                "meta_title_ja": (
                    meta_title_ja
                ),

                "url": url,

                "index_name": index_name,

            })

    return results


# =============================================================================
# 一覧HTML 1ファイル処理
# =============================================================================

def process_index(
    index_name,
    file_name
):
    """
    1つの一覧HTMLを読み込んで解析する。
    """

    html_path = os.path.join(
        INDEX_HTML_DIR,
        file_name
    )

    if not os.path.exists(
        html_path
    ):

        print()

        print(
            "【警告】HTMLが見つかりません:"
        )

        print(
            f"  {html_path}"
        )

        return []

    print()

    print(
        "-" * 70
    )

    print(
        f"解析: {file_name}"
    )

    print(
        "-" * 70
    )

    # -------------------------------------------------------------------------
    # HTML読み込み
    # -------------------------------------------------------------------------

    with open(
        html_path,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as file:

        html = file.read()

    # -------------------------------------------------------------------------
    # 抽出
    # -------------------------------------------------------------------------

    links = extract_links_from_html(
        html,
        index_name
    )

    print(
        f"  抽出件数: "
        f"{len(links)}"
    )

    # -------------------------------------------------------------------------
    # メタタイトル統計
    # -------------------------------------------------------------------------

    meta_count = sum(
        1
        for row in links
        if row.get(
            "meta_title",
            ""
        )
    )

    meta_ja_count = sum(
        1
        for row in links
        if row.get(
            "meta_title_ja",
            ""
        )
    )

    print(
        f"  メタタイトル取得: "
        f"{meta_count}"
    )

    print(
        f"  日本語名取得: "
        f"{meta_ja_count}"
    )

    # -------------------------------------------------------------------------
    # プレビュー
    # -------------------------------------------------------------------------

    preview_count = min(
        10,
        len(links)
    )

    for i in range(
        preview_count
    ):

        item = links[i]

        text = (
            f"  [{i + 1:04d}] "
            f"{item['title']}"
        )

        if item.get(
            "meta_title",
            ""
        ):

            text += (
                f' → "{item["meta_title"]}"'
            )

        if item.get(
            "meta_title_ja",
            ""
        ):

            text += (
                f' ({item["meta_title_ja"]})'
            )

        print(
            text
        )

    if len(links) > preview_count:

        print(
            f"  ... "
            f"(残り "
            f"{len(links) - preview_count}"
            f" 件)"
        )

    return links


# =============================================================================
# CSV保存
# =============================================================================

def save_csv(
    file_path,
    rows,
    fieldnames
):
    """
    UTF-8 BOM付きCSVで保存する。
    """

    os.makedirs(
        os.path.dirname(
            file_path
        ),
        exist_ok=True
    )

    with open(
        file_path,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            rows
        )


# =============================================================================
# URL統合
# =============================================================================

def merge_link_rows(
    all_rows
):
    """
    URL単位で重複を統合する。

    同一URLが複数一覧に存在する場合:

        indexes = hierarchy|normal_eta

    とする。

    meta_title / meta_title_ja は、
    空欄でない値を優先して保持する。
    """

    merged = {}

    for row in all_rows:

        url = row.get(
            "url",
            ""
        )

        if is_excluded_url(
            url
        ):

            continue

        title = clean_text(
            row.get(
                "title",
                ""
            )
        )

        if is_excluded_title(
            title
        ):

            continue

        meta_title = clean_meta_text(
            row.get(
                "meta_title",
                ""
            )
        )

        meta_title_ja = clean_meta_text(
            row.get(
                "meta_title_ja",
                ""
            )
        )

        index_name = clean_text(
            row.get(
                "index_name",
                ""
            )
        )

        # ---------------------------------------------------------------------
        # 新規
        # ---------------------------------------------------------------------

        if url not in merged:

            merged[url] = {

                "title": title,

                "meta_title": meta_title,

                "meta_title_ja": meta_title_ja,

                "url": url,

                "indexes": (
                    [index_name]
                    if index_name
                    else []
                ),

            }

            continue

        # ---------------------------------------------------------------------
        # title
        # ---------------------------------------------------------------------

        if (
            not merged[url]["title"]
            and title
        ):

            merged[url]["title"] = title

        # ---------------------------------------------------------------------
        # meta_title
        # ---------------------------------------------------------------------

        if (
            not merged[url]["meta_title"]
            and meta_title
        ):

            merged[url]["meta_title"] = (
                meta_title
            )

        # ---------------------------------------------------------------------
        # meta_title_ja
        # ---------------------------------------------------------------------

        if (
            not merged[url]["meta_title_ja"]
            and meta_title_ja
        ):

            merged[url]["meta_title_ja"] = (
                meta_title_ja
            )

        # ---------------------------------------------------------------------
        # indexes
        # ---------------------------------------------------------------------

        if (
            index_name
            and index_name
            not in merged[url]["indexes"]
        ):

            merged[url]["indexes"].append(
                index_name
            )

    result = []

    for item in merged.values():

        result.append({

            "title": item["title"],

            "meta_title": (
                item["meta_title"]
            ),

            "meta_title_ja": (
                item["meta_title_ja"]
            ),

            "url": item["url"],

            "indexes": "|".join(
                item["indexes"]
            ),

        })

    # -------------------------------------------------------------------------
    # タイトル順
    # -------------------------------------------------------------------------

    result.sort(
        key=lambda x: (
            x["title"].casefold(),
            x["url"]
        )
    )

    return result


# =============================================================================
# 全リンク統合
# =============================================================================

def build_all_links(
    all_rows
):
    """
    全抽出リンクをURL単位で統合する。
    """

    return merge_link_rows(
        all_rows
    )


# =============================================================================
# 記事候補DB
# =============================================================================

def build_pages_database(
    all_rows
):
    """
    一覧・案内・特殊ページを除外し、
    実記事候補DBを作る。
    """

    filtered_rows = []

    for row in all_rows:

        title = clean_text(
            row.get(
                "title",
                ""
            )
        )

        url = row.get(
            "url",
            ""
        )

        if is_excluded_url(
            url
        ):

            continue

        if is_excluded_title(
            title
        ):

            continue

        filtered_rows.append(
            row
        )

    return merge_link_rows(
        filtered_rows
    )


# =============================================================================
# TXT保存
# =============================================================================

def save_txt(
    file_path,
    title,
    rows
):
    """
    人間が確認しやすいTXTを生成する。
    """

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            "=" * 80
            + "\n"
        )

        file.write(
            f"{title}\n"
        )

        file.write(
            f"件数: {len(rows)}\n"
        )

        file.write(
            "=" * 80
            + "\n\n"
        )

        for i, row in enumerate(
            rows,
            start=1
        ):

            file.write(
                f"[{i:04d}] "
                f"{row['title']}\n"
            )

            if row.get(
                "meta_title",
                ""
            ):

                file.write(
                    f"      "
                    f"メタタイトル: "
                    f"{row['meta_title']}\n"
                )

            if row.get(
                "meta_title_ja",
                ""
            ):

                file.write(
                    f"      "
                    f"日本語名: "
                    f"{row['meta_title_ja']}\n"
                )

            file.write(
                f"      "
                f"{row['url']}\n"
            )

            if row.get(
                "indexes",
                ""
            ):

                file.write(
                    f"      一覧: "
                    f"{row['indexes']}\n"
                )

            file.write(
                "\n"
            )


# =============================================================================
# 統計
# =============================================================================

def print_statistics(
    rows,
    label
):
    """
    メタタイトル取得統計を表示する。
    """

    total = len(
        rows
    )

    meta_count = sum(
        1
        for row in rows
        if row.get(
            "meta_title",
            ""
        )
    )

    meta_ja_count = sum(
        1
        for row in rows
        if row.get(
            "meta_title_ja",
            ""
        )
    )

    print()

    print(
        f"{label}: "
        f"{total} 件"
    )

    print(
        f"メタタイトル取得: "
        f"{meta_count} 件"
    )

    print(
        f"日本語名取得: "
        f"{meta_ja_count} 件"
    )

    print(
        f"メタタイトル未取得: "
        f"{total - meta_count} 件"
    )

    print(
        f"日本語名未取得: "
        f"{total - meta_ja_count} 件"
    )


# =============================================================================
# メイン
# =============================================================================

def main():

    print()

    print(
        "=" * 80
    )

    print(
        " Backrooms Fandom JP - Index Parser"
    )

    print(
        "=" * 80
    )

    print()

    print(
        "入力フォルダ:"
    )

    print(
        f"  {INDEX_HTML_DIR}"
    )

    print()

    print(
        "出力フォルダ:"
    )

    print(
        f"  {OUTPUT_DIR}"
    )

    # =========================================================================
    # 出力フォルダ
    # =========================================================================

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    # =========================================================================
    # 一覧HTML解析
    # =========================================================================

    all_rows = []

    for index_name, file_name in (
        INDEX_FILES.items()
    ):

        rows = process_index(
            index_name,
            file_name
        )

        all_rows.extend(
            rows
        )

    print()

    print(
        "=" * 70
    )

    print(
        f"一覧ページからの抽出総数: "
        f"{len(all_rows)}"
    )

    print(
        "=" * 70
    )

    # =========================================================================
    # 全リンク統合
    # =========================================================================

    all_links = build_all_links(
        all_rows
    )

    save_csv(
        ALL_LINKS_CSV,
        all_links,
        [
            "title",
            "meta_title",
            "meta_title_ja",
            "url",
            "indexes",
        ]
    )

    print_statistics(
        all_links,
        "重複統合後の全リンク"
    )

    print()

    print(
        f"保存: "
        f"{ALL_LINKS_CSV}"
    )

    # =========================================================================
    # 記事候補DB
    # =========================================================================

    pages_database = build_pages_database(
        all_rows
    )

    save_csv(
        PAGES_DATABASE_CSV,
        pages_database,
        [
            "title",
            "meta_title",
            "meta_title_ja",
            "url",
            "indexes",
        ]
    )

    print_statistics(
        pages_database,
        "記事候補"
    )

    print()

    print(
        f"保存: "
        f"{PAGES_DATABASE_CSV}"
    )

    # =========================================================================
    # TXT
    # =========================================================================

    all_links_txt = os.path.join(
        OUTPUT_DIR,
        "all_links.txt"
    )

    pages_database_txt = os.path.join(
        OUTPUT_DIR,
        "pages_database.txt"
    )

    save_txt(
        all_links_txt,
        "Backrooms Fandom JP - All Extracted Links",
        all_links
    )

    save_txt(
        pages_database_txt,
        "Backrooms Fandom JP - Pages Database",
        pages_database
    )

    print()

    print(
        f"保存: "
        f"{all_links_txt}"
    )

    print(
        f"保存: "
        f"{pages_database_txt}"
    )

    # =========================================================================
    # 完了
    # =========================================================================

    print()

    print(
        "=" * 80
    )

    print(
        "解析・データベース生成完了"
    )

    print(
        "=" * 80
    )

    print()

    print(
        "生成ファイル:"
    )

    print(
        f"  1. {ALL_LINKS_CSV}"
    )

    print(
        f"  2. {PAGES_DATABASE_CSV}"
    )

    print(
        f"  3. {all_links_txt}"
    )

    print(
        f"  4. {pages_database_txt}"
    )

    print()


# =============================================================================
# 実行
# =============================================================================

if __name__ == "__main__":

    main()
