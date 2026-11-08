"""
Backrooms Navigation System - Fandom JP
Local / Master Database Module

ローカル保存されたFandom JPのLevelデータを検索・閲覧する。

Master Database:
    parsed_index/pages_master.csv

Legacy:
    wiki_list.txt
    extracted_fandom_levels_summary.csv

Local Data:
    downloaded_fandom_html/
    extracted_fandom_levels/
    downloaded_images/

Master Databaseを優先して検索し、
ローカルに保存済みのHTML/TXT/画像と組み合わせて表示する。
"""

import csv
import re
import webbrowser
from pathlib import Path


# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
    .parent
)


# =============================================================================
# Master Database
# =============================================================================

MASTER_FILE = (
    BASE_DIR
    / "parsed_index"
    / "pages_master.csv"
)


# =============================================================================
# Legacy Database
# =============================================================================

WIKI_LIST_FILE = (
    BASE_DIR
    / "wiki_list.txt"
)

SUMMARY_FILE = (
    BASE_DIR
    / "extracted_fandom_levels_summary.csv"
)


# =============================================================================
# Local Data
# =============================================================================

HTML_DB_DIR = (
    BASE_DIR
    / "downloaded_fandom_html"
)

TEXT_DB_DIR = (
    BASE_DIR
    / "extracted_fandom_levels"
)

IMAGE_DIR = (
    BASE_DIR
    / "downloaded_images"
)


# =============================================================================
# 共通
# =============================================================================

def clean_value(value):
    """
    CSVなどの値から余分な引用符・空白を除去する。
    """

    if value is None:
        return ""

    value = str(value)

    if not value:
        return ""

    return value.strip().strip(
        '"\'”’“‘'
    ).strip()


def sanitize_filename(name):
    """
    Windowsで使用できない文字を置換する。
    """

    return re.sub(
        r'[\\/*?:"<>|]',
        "_",
        name
    ).rstrip(" .")


# =============================================================================
# pages_master.csv
# =============================================================================

def load_master_database():
    """
    pages_master.csvからMaster Databaseを読み込む。

    Returns
    -------
    dict
        URL -> ページ情報
    """

    database = {}

    if not MASTER_FILE.exists():
        return database

    try:

        with open(
            MASTER_FILE,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(
                file
            )

            for row in reader:

                url = clean_value(
                    row.get(
                        "url",
                        ""
                    )
                )

                if not url:
                    continue

                database[url] = {
                    "title": clean_value(
                        row.get(
                            "title",
                            ""
                        )
                    ),

                    "url": url,

                    "category": clean_value(
                        row.get(
                            "category",
                            ""
                        )
                    ),

                    "language": clean_value(
                        row.get(
                            "language",
                            ""
                        )
                    ),

                    "source_type": clean_value(
                        row.get(
                            "source_type",
                            ""
                        )
                    ),

                    "indexes": clean_value(
                        row.get(
                            "indexes",
                            ""
                        )
                    ),

                    "translation_section": clean_value(
                        row.get(
                            "translation_section",
                            ""
                        )
                    ),

                    "section_number": clean_value(
                        row.get(
                            "section_number",
                            ""
                        )
                    ),

                    "classification_reason": clean_value(
                        row.get(
                            "classification_reason",
                            ""
                        )
                    ),
                }

    except (
        OSError,
        csv.Error
    ):
        return database

    return database


# =============================================================================
# Master Database検索
# =============================================================================

def find_master_candidates(
    query,
    language=None,
    category=None
):
    """
    pages_master.csvからページ候補を検索する。

    Parameters
    ----------
    query : str
        ページタイトル・Level名などの検索文字列

    language : str | None
        言語指定。
        例:
            JP
            EN
            PL
            UA
            ZH
            FR

    category : str | None
        カテゴリ指定。
        例:
            normal
            normal_eta
            sublevel
            sublevel_eta
            anomalous
            anomalous_eta

    Returns
    -------
    list[dict]
        Master DBのページ情報
    """

    database = load_master_database()

    if not database:
        return []

    query = query.strip()

    if not query:
        return []

    query_lower = query.casefold()

    normalized_language = (
        language.strip().casefold()
        if language
        else None
    )

    normalized_category = (
        category.strip().casefold()
        if category
        else None
    )

    results = []

    for page in database.values():

        # ---------------------------------------------------------------------
        # 言語フィルター
        # ---------------------------------------------------------------------

        if normalized_language:

            page_language = (
                page["language"]
                .casefold()
            )

            if page_language != normalized_language:
                continue

        # ---------------------------------------------------------------------
        # カテゴリフィルター
        # ---------------------------------------------------------------------

        if normalized_category:

            page_categories = [
                value.strip().casefold()
                for value in page[
                    "category"
                ].split("|")
                if value.strip()
            ]

            if normalized_category not in page_categories:
                continue

        # ---------------------------------------------------------------------
        # タイトル
        # ---------------------------------------------------------------------

        title = page["title"]

        if query_lower in title.casefold():

            results.append(
                page
            )

            continue

        # ---------------------------------------------------------------------
        # URL
        # ---------------------------------------------------------------------

        if query_lower in page["url"].casefold():

            results.append(
                page
            )

            continue

    results.sort(
        key=lambda page: (
            page["title"].casefold(),
            page["url"]
        )
    )

    return results


# =============================================================================
# wiki_list.txt
# =============================================================================

def load_wiki_titles():
    """
    wiki_list.txtから

        Level ID -> メタタイトル

    の辞書を作る。

    旧形式との互換性のため残している。
    """

    titles = {}

    if not WIKI_LIST_FILE.exists():
        return titles

    with open(
        WIKI_LIST_FILE,
        "r",
        encoding="utf-8-sig",
        errors="ignore"
    ) as file:

        for line in file:

            line = line.strip()

            if not line or ":" not in line:
                continue

            left, right = line.split(
                ":",
                1
            )

            node_id = left.strip()

            title = clean_value(
                right
            )

            if node_id and title:

                titles[node_id] = title

    return titles


# =============================================================================
# summary CSV
# =============================================================================

def load_summary():
    """
    extracted_fandom_levels_summary.csvから
    Levelのメタ情報を読み込む。

    旧形式との互換性のため残している。
    """

    summary = {}

    if not SUMMARY_FILE.exists():
        return summary

    with open(
        SUMMARY_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(
            file
        )

        for row in reader:

            level_id = clean_value(
                row.get(
                    "階層コード",
                    ""
                )
            )

            if not level_id:
                continue

            summary[level_id] = {

                "title": clean_value(
                    row.get(
                        "メタタイトル",
                        ""
                    )
                ),

                "danger": clean_value(
                    row.get(
                        "危険度",
                        ""
                    )
                ),

                "comprehension": clean_value(
                    row.get(
                        "理解度",
                        ""
                    )
                ),
            }

    return summary


# =============================================================================
# Level ID → HTMLファイル名プレフィックス
# =============================================================================

def get_html_filename_prefix(level_id):
    """
    Level IDをHTML保存時のファイル名形式へ変換する。

    例:
        Level 1250 η
        → Level_1250_η

        Level -753 η
        → Level_-753_η

        Level 420.1 η
        → Level_420.1_η
    """

    match = re.fullmatch(
        r"Level\s+(-?\d+(?:\.\d+)?)\s+η",
        level_id
    )

    if match:

        number = match.group(
            1
        )

        return (
            f"Level_{number}_η"
        )

    return level_id


# =============================================================================
# HTMLファイル検索
# =============================================================================

def find_html_database(level_id):
    """
    Level IDに対応するHTMLファイルを探す。

    実際の保存名が、

        Level_1250_η - タイトル.html

    のような形式でも認識する。

    また、

        Level 1250 η - タイトル.html

    のようなスペース形式にも対応する。
    """

    if not HTML_DB_DIR.exists():
        return None

    normalized_prefix = (
        get_html_filename_prefix(
            level_id
        )
    )

    candidates = []

    for path in HTML_DB_DIR.glob(
        "*.html"
    ):

        filename = path.name

        # ---------------------------------------------------------------------
        # アンダースコア形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            normalized_prefix
        ):

            candidates.append(
                path
            )

            continue

        # ---------------------------------------------------------------------
        # スペース形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            level_id
        ):

            candidates.append(
                path
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda path: len(
            path.name
        )
    )

    return candidates[0]


# =============================================================================
# TXTファイル検索
# =============================================================================

def find_text_database(level_id):
    """
    Level IDに対応する本文TXTを探す。
    """

    if not TEXT_DB_DIR.exists():
        return None

    candidates = []

    normalized_prefix = (
        get_html_filename_prefix(
            level_id
        )
    )

    for path in TEXT_DB_DIR.glob(
        "*.txt"
    ):

        filename = path.name

        # ---------------------------------------------------------------------
        # スペース形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            level_id
        ):

            candidates.append(
                path
            )

            continue

        # ---------------------------------------------------------------------
        # アンダースコア形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            normalized_prefix
        ):

            candidates.append(
                path
            )

    if not candidates:
        return None

    candidates.sort(
        key=lambda path: len(
            path.name
        )
    )

    return candidates[0]


# =============================================================================
# 画像検索
# =============================================================================

def find_level_images(level_id):
    """
    downloaded_images/から対象Levelの画像を探す。
    """

    if not IMAGE_DIR.exists():
        return []

    images = []

    safe_level_id = sanitize_filename(
        level_id
    )

    normalized_prefix = (
        get_html_filename_prefix(
            level_id
        )
    )

    for path in IMAGE_DIR.iterdir():

        if not path.is_file():
            continue

        if path.suffix.lower() not in {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp"
        }:
            continue

        filename = path.name

        # ---------------------------------------------------------------------
        # スペース形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            level_id
        ):

            images.append(
                path
            )

            continue

        # ---------------------------------------------------------------------
        # アンダースコア形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            normalized_prefix
        ):

            images.append(
                path
            )

            continue

        # ---------------------------------------------------------------------
        # Windows禁止文字置換形式
        # ---------------------------------------------------------------------

        if filename.startswith(
            safe_level_id
        ):

            images.append(
                path
            )

    images.sort(
        key=lambda path: path.name
    )

    return images


# =============================================================================
# Master DBページ検索
# =============================================================================

def find_master_page(
    level_id
):
    """
    Master DBからタイトル完全一致のページを探す。
    """

    database = load_master_database()

    if not database:
        return None

    level_id_lower = (
        level_id.casefold()
    )

    for page in database.values():

        if (
            page["title"].casefold()
            == level_id_lower
        ):

            return page

    return None


# =============================================================================
# Level情報取得
# =============================================================================

def get_level_info(level_id):
    """
    Master DBとローカルデータを統合してLevel情報を返す。
    """

    wiki_titles = load_wiki_titles()
    summary = load_summary()

    master_info = find_master_page(
        level_id
    )

    summary_info = summary.get(
        level_id,
        {}
    )

    # -------------------------------------------------------------------------
    # タイトル
    # -------------------------------------------------------------------------

    title = summary_info.get(
        "title",
        ""
    )

    if not title:

        title = wiki_titles.get(
            level_id,
            ""
        )

    if (
        not title
        and master_info
    ):

        title = master_info.get(
            "title",
            ""
        )

    # -------------------------------------------------------------------------
    # ローカルファイル
    # -------------------------------------------------------------------------

    html_file = (
        find_html_database(
            level_id
        )
    )

    text_file = (
        find_text_database(
            level_id
        )
    )

    images = (
        find_level_images(
            level_id
        )
    )

    # -------------------------------------------------------------------------
    # Master DB情報
    # -------------------------------------------------------------------------

    master_url = ""
    master_category = ""
    master_language = ""
    master_source_type = ""
    master_indexes = ""
    master_translation_section = ""
    master_section_number = ""

    if master_info:

        master_url = master_info.get(
            "url",
            ""
        )

        master_category = master_info.get(
            "category",
            ""
        )

        master_language = master_info.get(
            "language",
            ""
        )

        master_source_type = master_info.get(
            "source_type",
            ""
        )

        master_indexes = master_info.get(
            "indexes",
            ""
        )

        master_translation_section = (
            master_info.get(
                "translation_section",
                ""
            )
        )

        master_section_number = (
            master_info.get(
                "section_number",
                ""
            )
        )

    return {
        "level_id": level_id,

        "title": title,

        "url": master_url,

        "category": master_category,

        "language": master_language,

        "source_type": master_source_type,

        "indexes": master_indexes,

        "translation_section": (
            master_translation_section
        ),

        "section_number": (
            master_section_number
        ),

        "danger": summary_info.get(
            "danger",
            ""
        ),

        "comprehension": summary_info.get(
            "comprehension",
            ""
        ),

        "html_file": html_file,

        "text_file": text_file,

        "images": images,

        "html_exists": (
            html_file is not None
        ),

        "text_exists": (
            text_file is not None
        ),

        "image_exists": (
            len(images) > 0
        ),
    }


# =============================================================================
# Level候補検索
# =============================================================================

def find_level_candidates(
    query,
    language=None,
    category=None
):
    """
    Master Databaseを優先してLevel候補を検索する。

    Master DBに候補が存在しない場合は、
    従来のwiki_list.txt / summary / HTMLからも候補を探す。

    Master DB使用時:
        list[dict]

    Legacy fallback時:
        list[str]
    """

    query = query.strip()

    if not query:
        return []

    # =========================================================================
    # 1. Master Database
    # =========================================================================

    master_candidates = (
        find_master_candidates(
            query,
            language=language,
            category=category
        )
    )

    if master_candidates:

        return master_candidates

    # =========================================================================
    # 2. Legacy fallback
    # =========================================================================

    candidates = set()

    # -------------------------------------------------------------------------
    # wiki_list.txt
    # -------------------------------------------------------------------------

    wiki_titles = (
        load_wiki_titles()
    )

    candidates.update(
        wiki_titles.keys()
    )

    # -------------------------------------------------------------------------
    # summary CSV
    # -------------------------------------------------------------------------

    summary = (
        load_summary()
    )

    candidates.update(
        summary.keys()
    )

    # -------------------------------------------------------------------------
    # HTMLファイル
    # -------------------------------------------------------------------------

    if HTML_DB_DIR.exists():

        for path in HTML_DB_DIR.glob(
            "*.html"
        ):

            filename = path.stem

            match = re.match(
                r"^(Level[_\s]+-?\d+(?:\.\d+)?"
                r"(?:[_\s]+η)?"
                r"(?:[_\s]+\(\d+\))?)",
                filename
            )

            if match:

                level_id = (
                    match.group(
                        1
                    )
                )

                level_id = (
                    level_id.replace(
                        "_",
                        " "
                    )
                )

                candidates.add(
                    level_id
                )

    query_lower = (
        query.casefold()
    )

    # =========================================================================
    # 完全一致
    # =========================================================================

    exact = [
        level
        for level in candidates
        if level.casefold()
        == query_lower
    ]

    if exact:
        return sorted(
            exact
        )

    # =========================================================================
    # 数字だけ
    # =========================================================================

    if query.lstrip("-").isdigit():

        normal = (
            f"Level {query}"
        )

        if normal in candidates:

            return [
                normal
            ]

        eta = (
            f"Level {query} η"
        )

        if eta in candidates:

            return [
                eta
            ]

    # =========================================================================
    # 部分一致
    # =========================================================================

    partial = [
        level
        for level in candidates
        if query_lower
        in level.casefold()
    ]

    return sorted(
        partial
    )


# =============================================================================
# Level情報表示
# =============================================================================

def print_level_info(info):
    """
    Levelの基本情報を表示する。
    """

    level_id = (
        info["level_id"]
    )

    title = (
        info["title"]
    )

    print()
    print(
        "=" * 80
    )

    if title:

        print(
            f'{level_id} "{title}"'
        )

    else:

        print(
            level_id
        )

    print(
        "=" * 80
    )

    # -------------------------------------------------------------------------
    # Master DB
    # -------------------------------------------------------------------------

    print(
        f"カテゴリ      : "
        f"{info.get('category') or '情報なし'}"
    )

    print(
        f"言語          : "
        f"{info.get('language') or '情報なし'}"
    )

    print(
        f"ソース        : "
        f"{info.get('source_type') or '情報なし'}"
    )

    if info.get("url"):

        print(
            f"URL           : "
            f"{info['url']}"
        )

    else:

        print(
            "URL           : 情報なし"
        )

    # -------------------------------------------------------------------------
    # Legacy情報
    # -------------------------------------------------------------------------

    print(
        f"危険度        : "
        f"{info.get('danger') or '情報なし'}"
    )

    print(
        f"理解度        : "
        f"{info.get('comprehension') or '情報なし'}"
    )

    # -------------------------------------------------------------------------
    # 一覧情報
    # -------------------------------------------------------------------------

    if info.get(
        "indexes"
    ):

        print(
            f"掲載一覧      : "
            f"{info['indexes']}"
        )

    if info.get(
        "translation_section"
    ):

        print(
            f"翻訳一覧区分  : "
            f"{info['translation_section']}"
        )

    # -------------------------------------------------------------------------
    # ローカルデータ
    # -------------------------------------------------------------------------

    print()
    print(
        "【 ローカルデータ 】"
    )

    print(
        f"HTML          : "
        f"{'あり' if info['html_file'] else 'なし'}"
    )

    print(
        f"本文          : "
        f"{'あり' if info['text_file'] else 'なし'}"
    )

    print(
        f"画像          : "
        f"{len(info['images'])} 枚"
    )

    if info["html_file"]:

        print(
            f"HTMLファイル  : "
            f"{info['html_file'].name}"
        )

    if info["text_file"]:

        print(
            f"本文ファイル  : "
            f"{info['text_file'].name}"
        )

    print(
        "=" * 80
    )


# =============================================================================
# 本文表示
# =============================================================================

def display_text_database(
    text_file
):
    """
    LevelのローカルTXT本文を表示する。
    """

    if text_file is None:

        print()
        print(
            "⚠ このLevelの本文データはありません。"
        )

        return

    try:

        with open(
            text_file,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as file:

            content = file.read()

    except OSError as e:

        print()
        print(
            "⚠ 本文データの読み込みに失敗しました。"
        )

        print(
            e
        )

        return

    print()
    print(
        "=" * 80
    )

    print(
        f"【ローカルデータベース】 "
        f"{text_file.name}"
    )

    print(
        "=" * 80
    )

    print()

    print(
        content
    )

    print()

    print(
        "=" * 80
    )

    print(
        "【本文終端】"
    )

    print(
        "=" * 80
    )


# =============================================================================
# Fandomページをブラウザで開く
# =============================================================================

def open_fandom_page(
    url
):
    """
    Fandom URLを既定ブラウザで開く。
    """

    if not url:

        print()
        print(
            "⚠ Fandom URLがありません。"
        )

        return False

    try:

        webbrowser.open(
            url
        )

        print()
        print(
            "ブラウザでFandomページを開きました。"
        )

        return True

    except Exception as e:

        print()
        print(
            "⚠ ブラウザを開けませんでした。"
        )

        print(
            e
        )

        return False


# =============================================================================
# ローカルHTMLをブラウザで開く
# =============================================================================

def open_local_html(
    html_file
):
    """
    ローカルHTMLを既定ブラウザで開く。
    """

    if html_file is None:

        print()
        print(
            "⚠ このページのローカルHTMLはありません。"
        )

        return False

    try:

        webbrowser.open(
            html_file.resolve().as_uri()
        )

        print()
        print(
            "ローカルHTMLをブラウザで開きました。"
        )

        return True

    except Exception as e:

        print()
        print(
            "⚠ ローカルHTMLを開けませんでした。"
        )

        print(
            e
        )

        return False


# =============================================================================
# ページ操作メニュー
# =============================================================================

def page_action_menu(
    info
):
    """
    選択したページに対する操作メニュー。
    """

    while True:

        print()
        print(
            "=" * 80
        )

        print(
            "ページ操作"
        )

        print(
            "=" * 80
        )

        print(
            f"ページ: "
            f"{info['level_id']}"
        )

        if info.get("title"):

            print(
                f"タイトル: "
                f"{info['title']}"
            )

        print()

        print(
            "[1] ページ情報を表示"
        )

        print(
            "[2] ローカル本文を表示"
        )

        print(
            "[3] Fandomページをブラウザで開く"
        )

        print(
            "[4] ローカルHTMLをブラウザで開く"
        )

        print(
            "[0] 検索結果へ戻る"
        )

        print()

        choice = input(
            "▶選択: "
        ).strip()

        # ---------------------------------------------------------------------
        # 情報
        # ---------------------------------------------------------------------

        if choice == "1":

            print_level_info(
                info
            )

        # ---------------------------------------------------------------------
        # 本文
        # ---------------------------------------------------------------------

        elif choice == "2":

            if info["text_file"]:

                display_text_database(
                    info["text_file"]
                )

            else:

                print()
                print(
                    "⚠ ローカル本文データがありません。"
                )

        # ---------------------------------------------------------------------
        # Fandom
        # ---------------------------------------------------------------------

        elif choice == "3":

            open_fandom_page(
                info.get(
                    "url",
                    ""
                )
            )

        # ---------------------------------------------------------------------
        # Local HTML
        # ---------------------------------------------------------------------

        elif choice == "4":

            open_local_html(
                info.get(
                    "html_file"
                )
            )

        # ---------------------------------------------------------------------
        # 戻る
        # ---------------------------------------------------------------------

        elif choice == "0":

            return

        else:

            print()
            print(
                "1 / 2 / 3 / 4 / 0 "
                "のいずれかを入力してください。"
            )


# =============================================================================
# ローカルデータベースブラウザー
# =============================================================================

def browse_database():
    """
    Master Databaseを中心にページを検索・閲覧する。

    Master DBに存在するが、
    ローカルHTML未取得のページも検索できる。
    """

    print()
    print(
        "=" * 80
    )

    print(
        "      Backrooms Fandom JP - Local / Master Database"
    )

    print(
        "=" * 80
    )

    master = (
        load_master_database()
    )

    if master:

        print()
        print(
            f"Master Database: "
            f"{len(master)} 件"
        )

    else:

        print()
        print(
            "⚠ Master Databaseが見つかりません。"
        )

        print(
            "旧ローカルDBを使用します。"
        )

    while True:

        print()

        query = input(
            "▶ Level・ページ名を入力 "
            "(空欄で終了) : "
        ).strip()

        if not query:

            print()
            print(
                "データベースを終了します。"
            )

            break

        candidates = (
            find_level_candidates(
                query
            )
        )

        if not candidates:

            print()
            print(
                f"❌ ページが見つかりません: "
                f"{query}"
            )

            continue

        # =========================================================================
        # Master DB形式
        # =========================================================================

        if (
            candidates
            and isinstance(
                candidates[0],
                dict
            )
        ):

            # ---------------------------------------------------------------------
            # 1件
            # ---------------------------------------------------------------------

            if len(candidates) == 1:

                selected = (
                    candidates[0]
                )

            # ---------------------------------------------------------------------
            # 複数候補
            # ---------------------------------------------------------------------

            else:

                print()
                print(
                    "候補が複数あります。"
                )

                visible_count = min(
                    len(candidates),
                    30
                )

                for index, page in enumerate(
                    candidates[
                        :visible_count
                    ],
                    1
                ):

                    print(
                        f"  [{index}] "
                        f"{page['title']} "
                        f"[{page['language'] or '-'}] "
                        f"[{page['category'] or '-'}]"
                    )

                if len(candidates) > 30:

                    print(
                        f"  ... 他 "
                        f"{len(candidates) - 30} 件"
                    )

                selected = None

                while True:

                    choice = input(
                        "番号を入力してください"
                        "（Enterでキャンセル）: "
                    ).strip()

                    if not choice:

                        break

                    if not choice.isdigit():

                        print(
                            "数字を入力してください。"
                        )

                        continue

                    index = (
                        int(choice) - 1
                    )

                    if (
                        0
                        <= index
                        < visible_count
                    ):

                        selected = (
                            candidates[index]
                        )

                        break

                    print(
                        "範囲外の番号です。"
                    )

                if selected is None:

                    continue

            # ---------------------------------------------------------------------
            # Master DBからタイトル取得
            # ---------------------------------------------------------------------

            level_id = (
                selected["title"]
            )

        # =========================================================================
        # Legacy形式
        # =========================================================================

        else:

            if len(candidates) == 1:

                level_id = (
                    candidates[0]
                )

            else:

                print()
                print(
                    "候補が複数あります。"
                )

                visible_count = min(
                    len(candidates),
                    30
                )

                for index, candidate in enumerate(
                    candidates[:visible_count],
                    1
                ):

                    print(
                        f"  [{index}] "
                        f"{candidate}"
                    )

                if len(candidates) > 30:

                    print(
                        f"  ... 他 "
                        f"{len(candidates) - 30} 件"
                    )

                level_id = None

                while True:

                    choice = input(
                        "番号を入力してください"
                        "（Enterでキャンセル）: "
                    ).strip()

                    if not choice:

                        break

                    if not choice.isdigit():

                        print(
                            "数字を入力してください。"
                        )

                        continue

                    index = (
                        int(choice) - 1
                    )

                    if (
                        0
                        <= index
                        < visible_count
                    ):

                        level_id = (
                            candidates[index]
                        )

                        break

                    print(
                        "範囲外の番号です。"
                    )

                if level_id is None:

                    continue

        # =========================================================================
        # Level情報取得
        # =========================================================================

        info = get_level_info(
            level_id
        )

        # =========================================================================
        # 操作メニュー
        # =========================================================================

        page_action_menu(
            info
        )