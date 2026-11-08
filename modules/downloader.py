"""
Backrooms Navigation System - Fandom JP
HTML Downloader Module

Fandom JP のWikiページHTMLをダウンロードする。

基本データ:
    parsed_index/pages_master.csv

メニュー:
    [1] Master DBから検索・選択してダウンロード
    [2] 言語・カテゴリから選択してダウンロード
    [3] URLを直接指定してダウンロード
    [4] 言語版 → カテゴリ → 範囲/一覧選択ダウンロード
    [0] 終了

基本方針:
    Master DBに実在するページだけを対象とする。

例:
    Level 178 η
    Level 178.1 η
    Level -178 η
    Level PL-0
    Level ZH 0
    Level UA-(-0)
    Level UA-9¾
    Level !
    The Void
    翻訳/Level 178

カテゴリはMaster DB上では内部コードで保持する。

    normal
    normal_eta
    sublevel
    sublevel_eta
    anomalous
    anomalous_eta
    ...

画面表示時には、

    通常階層（JP）
    通常階層 η（JP）
    亜階層（JP）
    番外階層（UA）

などの表示に変換する。

[1]:
    自由検索
    → ヒットしたページから選択

[2]:
    言語版
    → その言語に実在するカテゴリ
    → ページ一覧から選択

[3]:
    URL直接指定

[4]:
    言語版
    → その言語に実在するカテゴリ
    → normal / normal_eta は整数範囲指定
    → その他カテゴリは一覧選択

通常階層の範囲指定では、
Master DBに存在するページだけを対象とするため、
存在しない番号への404アクセスを行わない。

HTMLには管理用BRNAVI_META_INFOコメントを埋め込む。
"""

import csv
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests


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

MASTER_FILE = (
    BASE_DIR
    / "parsed_index"
    / "pages_master.csv"
)

WIKI_LIST_FILE = (
    BASE_DIR
    / "wiki_list.txt"
)

OUTPUT_DIR = (
    BASE_DIR
    / "downloaded_fandom_html"
)

BASE_URL = (
    "https://backrooms.fandom.com/ja/wiki/"
)


# =============================================================================
# ダウンロード設定
# =============================================================================

DEFAULT_DELAY = 2.0

REQUEST_TIMEOUT = 15


# =============================================================================
# カテゴリ表示名
# =============================================================================

CATEGORY_LABELS = {
    "normal": "通常階層",
    "normal_eta": "通常階層 η",

    "sublevel": "亜階層",
    "sublevel_eta": "亜階層 η",

    "anomalous": "番外階層",
    "anomalous_eta": "番外階層 η",

    "foreign": "国外階層",

    "dimension": "次元",

    "entity": "エンティティ",

    "item": "物品",

    "phenomenon": "現象",

    "canon": "カノン",

    "joke_level": "ジョーク階層",
    "joke_article": "ジョーク記事",

    "foreign_joke": "国外ジョーク記事",

    "person": "人物",

    "unknown": "未分類",
}


# =============================================================================
# カテゴリ表示順
# =============================================================================

CATEGORY_ORDER = [
    "normal",
    "normal_eta",

    "sublevel",
    "sublevel_eta",

    "anomalous",
    "anomalous_eta",

    "foreign",

    "dimension",

    "entity",
    "item",
    "phenomenon",
    "canon",

    "joke_level",
    "joke_article",
    "foreign_joke",

    "person",
    "unknown",
]


# =============================================================================
# 共通
# =============================================================================

def clean_value(value):
    """
    CSVなどの値から余分な空白・引用符を除去する。
    """

    if value is None:
        return ""

    return str(value).strip().strip(
        '"\'”’“‘'
    ).strip()


def sanitize_filename(name):
    """
    Windowsで使用できない文字を _ に置き換える。
    """

    name = re.sub(
        r'[\\/*?:"<>|]',
        "_",
        name
    )

    name = name.rstrip(" .")

    if not name:
        name = "Fandom_Page"

    return name


# =============================================================================
# カテゴリ表示
# =============================================================================

def get_category_label(category):
    """
    categoryコードを日本語表示名に変換する。
    """

    return CATEGORY_LABELS.get(
        category,
        category
    )


def get_category_display_name(
    language,
    category
):
    """
    「通常階層（PL）」のような表示名を作る。
    """

    label = get_category_label(
        category
    )

    if language:
        return (
            f"{label}（{language}）"
        )

    return label


def get_category_sort_key(category):
    """
    カテゴリの表示順。
    """

    try:

        return (
            CATEGORY_ORDER.index(
                category
            ),
            category
        )

    except ValueError:

        return (
            len(CATEGORY_ORDER),
            category
        )


# =============================================================================
# Master Database
# =============================================================================

def load_master_database():
    """
    pages_master.csvを読み込む。

    Returns
    -------
    list[dict]
    """

    pages = []

    if not MASTER_FILE.exists():

        print()
        print(
            "【エラー】Master Databaseが見つかりません。"
        )

        print(
            f"  {MASTER_FILE}"
        )

        return pages

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

                parsed = urlparse(
                    url
                )

                if (
                    parsed.scheme
                    not in {"http", "https"}
                ):

                    continue

                pages.append({
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
                })

    except (
        OSError,
        csv.Error
    ) as e:

        print()
        print(
            "【エラー】Master Databaseの読み込みに失敗しました。"
        )

        print(
            f"  {e}"
        )

        return []

    return pages


# =============================================================================
# Master DB検索
# =============================================================================

def search_master(
    query="",
    language=None,
    category=None
):
    """
    Master DBを検索する。

    query:
        title / URL の部分一致

    language:
        JP / EN / PL / UA / ZH / FR ...

    category:
        normal / normal_eta / sublevel ...
    """

    pages = load_master_database()

    if not pages:
        return []

    query = clean_value(
        query
    ).casefold()

    language = (
        clean_value(
            language
        ).casefold()
        if language
        else ""
    )

    category = (
        clean_value(
            category
        ).casefold()
        if category
        else ""
    )

    results = []

    for page in pages:

        # ---------------------------------------------------------------------
        # Language
        # ---------------------------------------------------------------------

        if language:

            page_language = (
                page["language"].casefold()
            )

            if page_language != language:
                continue

        # ---------------------------------------------------------------------
        # Category
        # ---------------------------------------------------------------------

        if category:

            page_categories = {
                item.strip().casefold()
                for item in page["category"].split("|")
                if item.strip()
            }

            if category not in page_categories:
                continue

        # ---------------------------------------------------------------------
        # Query
        # ---------------------------------------------------------------------

        if query:

            title_match = (
                query
                in page["title"].casefold()
            )

            url_match = (
                query
                in page["url"].casefold()
            )

            if not (
                title_match
                or url_match
            ):

                continue

        results.append(
            page
        )

    results.sort(
        key=lambda page: (
            page["title"].casefold(),
            page["url"]
        )
    )

    return results


# =============================================================================
# ファイル名生成
# =============================================================================

def make_filename(
    page,
    used_names=None
):
    """
    Master DBページから保存ファイル名を生成する。

    例:
        Level 178 η
            → Level_178_η.html

        Level PL-0
            → Level_PL-0.html

        Level ZH 0
            → Level_ZH_0.html

        翻訳/Level 178
            → 翻訳_Level_178.html

        Level !
            → Level_!.html

        The Void
            → The_Void.html
    """

    title = clean_value(
        page.get(
            "title",
            ""
        )
    )

    if not title:

        title = "Fandom_Page"

    title = title.replace(
        "/",
        "_"
    )

    filename_base = sanitize_filename(
        title
    )

    filename_base = re.sub(
        r"\s+",
        "_",
        filename_base
    )

    filename = (
        f"{filename_base}.html"
    )

    if used_names is None:

        return filename

    if filename not in used_names:

        return filename

    counter = 1

    while True:

        candidate = (
            f"{filename_base}_{counter}.html"
        )

        if candidate not in used_names:

            return candidate

        counter += 1


# =============================================================================
# 既存HTML検索
# =============================================================================

def find_existing_html(page):
    """
    Master DBページに対応する既存HTMLを探す。

    優先:
        1. BRNAVI_META_INFOのURL
        2. タイトル
    """

    if not OUTPUT_DIR.exists():

        return None

    title = clean_value(
        page.get(
            "title",
            ""
        )
    )

    url = clean_value(
        page.get(
            "url",
            ""
        )
    )

    # -------------------------------------------------------------------------
    # 1. URL管理コメント
    # -------------------------------------------------------------------------

    if url:

        for path in OUTPUT_DIR.glob(
            "*.html"
        ):

            try:

                with open(
                    path,
                    "r",
                    encoding="utf-8",
                    errors="ignore"
                ) as file:

                    header = file.read(
                        4096
                    )

                if (
                    f"URL={url}"
                    in header
                ):

                    return path

            except OSError:

                continue

    # -------------------------------------------------------------------------
    # 2. タイトル
    # -------------------------------------------------------------------------

    if not title:

        return None

    safe_title = sanitize_filename(
        title.replace(
            "/",
            "_"
        )
    )

    candidates = []

    for path in OUTPUT_DIR.glob(
        "*.html"
    ):

        stem = path.stem

        if (
            stem == safe_title
            or stem.startswith(
                safe_title + "_"
            )
        ):

            candidates.append(
                path
            )

    if not candidates:

        return None

    candidates.sort(
        key=lambda path: (
            len(path.name),
            path.name.casefold()
        )
    )

    return candidates[0]


# =============================================================================
# HTML修正
# =============================================================================

def modify_html(
    html_content,
    page
):
    """
    HTMLへ管理用メタコメントを埋め込む。
    """

    soup = BeautifulSoup(
        html_content,
        "html.parser"
    )

    title = clean_value(
        page.get(
            "title",
            ""
        )
    )

    url = clean_value(
        page.get(
            "url",
            ""
        )
    )

    category = clean_value(
        page.get(
            "category",
            ""
        )
    )

    language = clean_value(
        page.get(
            "language",
            ""
        )
    )

    source_type = clean_value(
        page.get(
            "source_type",
            ""
        )
    )

    indexes = clean_value(
        page.get(
            "indexes",
            ""
        )
    )

    # -------------------------------------------------------------------------
    # HTML title
    # -------------------------------------------------------------------------

    if soup.title:

        original_title = (
            soup.title.string
            or ""
        ).strip()

        if (
            title
            and title not in original_title
        ):

            soup.title.string = (
                f"{title} | "
                f"{original_title}"
            )

    # -------------------------------------------------------------------------
    # 管理用コメント
    # -------------------------------------------------------------------------

    meta_comment = (
        "<!-- BRNAVI_META_INFO: "
        f"TITLE={title} | "
        f"URL={url} | "
        f"CATEGORY={category} | "
        f"LANGUAGE={language} | "
        f"SOURCE={source_type} | "
        f"INDEXES={indexes} "
        "-->\n"
    )

    return (
        meta_comment
        + str(soup)
    )


# =============================================================================
# URL直接ダウンロード
# =============================================================================

def download_url(
    url,
    page_info=None,
    delay=DEFAULT_DELAY,
    overwrite=False,
    show_detail=True
):
    """
    URLを直接ダウンロードする。
    """

    url = clean_value(
        url
    )

    if not url:

        print(
            "【エラー】URLが指定されていません。"
        )

        return None

    # -------------------------------------------------------------------------
    # page_infoがない場合
    # -------------------------------------------------------------------------

    if page_info is None:

        title = unquote(
            url.rstrip("/").split(
                "/"
            )[-1]
        )

        title = title.replace(
            "_",
            " "
        )

        page_info = {
            "title": title,
            "url": url,
            "category": "",
            "language": "",
            "source_type": "direct",
            "indexes": "",
        }

    # -------------------------------------------------------------------------
    # 保存先
    # -------------------------------------------------------------------------

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    existing = find_existing_html(
        page_info
    )

    if (
        existing is not None
        and not overwrite
    ):

        if show_detail:

            print(
                f"スキップ（取得済み）: "
                f"{existing.name}"
            )

        return existing

    # -------------------------------------------------------------------------
    # ファイル名
    # -------------------------------------------------------------------------

    used_names = {
        path.name
        for path in OUTPUT_DIR.glob(
            "*.html"
        )
    }

    if existing is not None:

        used_names.discard(
            existing.name
        )

    if (
        existing is not None
        and overwrite
    ):

        file_path = existing

    else:

        filename = make_filename(
            page_info,
            used_names
        )

        file_path = (
            OUTPUT_DIR
            / filename
        )

    # -------------------------------------------------------------------------
    # 表示
    # -------------------------------------------------------------------------

    if show_detail:

        print()
        print(
            "-" * 70
        )

        print(
            f"タイトル : "
            f"{page_info.get('title', '') or '(タイトルなし)'}"
        )

        print(
            f"言語     : "
            f"{page_info.get('language', '') or '-'}"
        )

        print(
            f"カテゴリ : "
            f"{page_info.get('category', '') or '-'}"
        )

        print(
            f"ソース   : "
            f"{page_info.get('source_type', '') or '-'}"
        )

        print(
            f"URL      : "
            f"{url}"
        )

        print(
            f"保存先   : "
            f"{file_path.name}"
        )

        print(
            "-" * 70
        )

    # -------------------------------------------------------------------------
    # HTTP
    # -------------------------------------------------------------------------

    try:

        response = requests.get(
            url,
            impersonate="chrome",
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code == 200:

            html_content = (
                response.text
            )

            modified_html = modify_html(
                html_content,
                page_info
            )

            with open(
                file_path,
                "w",
                encoding="utf-8"
            ) as file:

                file.write(
                    modified_html
                )

            if show_detail:

                print(
                    f"成功: "
                    f"{file_path.name}"
                )

            return file_path

        if show_detail:

            print(
                f"エラー "
                f"(ステータスコード: "
                f"{response.status_code})"
            )

        return None

    except Exception as e:

        if show_detail:

            print(
                f"通信エラー: "
                f"{e}"
            )

        return None

    finally:

        if delay > 0:

            time.sleep(
                delay
            )


# =============================================================================
# Master DBページ1件
# =============================================================================

def download_page(
    page,
    overwrite=False,
    delay=DEFAULT_DELAY,
    show_detail=True
):
    """
    Master DBの1ページをダウンロードする。
    """

    if not page:

        print(
            "【エラー】ページ情報がありません。"
        )

        return None

    url = clean_value(
        page.get(
            "url",
            ""
        )
    )

    if not url:

        print(
            "【エラー】このページにはURLがありません。"
        )

        return None

    return download_url(
        url,
        page_info=page,
        delay=delay,
        overwrite=overwrite,
        show_detail=show_detail
    )


# =============================================================================
# 複数ページ一括
# =============================================================================

def download_pages(
    pages,
    overwrite=False,
    delay=DEFAULT_DELAY
):
    """
    複数ページを順番にダウンロードする。

    Returns
    -------
    dict
        {
            "downloaded": int,
            "skipped": int,
            "failed": int
        }
    """

    result = {
        "downloaded": 0,
        "skipped": 0,
        "failed": 0,
    }

    total = len(
        pages
    )

    if total == 0:

        return result

    print()
    print(
        "=" * 80
    )

    print(
        f"HTML一括ダウンロード"
        f"（全 {total} 件）"
    )

    print(
        "=" * 80
    )

    for index, page in enumerate(
        pages,
        1
    ):

        print()
        print(
            f"[{index}/{total}]"
        )

        existing = find_existing_html(
            page
        )

        if (
            existing is not None
            and not overwrite
        ):

            returned = download_page(
                page,
                overwrite=False,
                delay=delay
            )

            if returned is not None:

                result["skipped"] += 1

            else:

                result["failed"] += 1

            continue

        returned = download_page(
            page,
            overwrite=overwrite,
            delay=delay
        )

        if returned is not None:

            result["downloaded"] += 1

        else:

            result["failed"] += 1

    print()
    print(
        "=" * 80
    )

    print(
        "一括ダウンロード完了"
    )

    print(
        f"新規取得 : "
        f"{result['downloaded']} 件"
    )

    print(
        f"取得済み : "
        f"{result['skipped']} 件"
    )

    print(
        f"失敗     : "
        f"{result['failed']} 件"
    )

    print(
        "=" * 80
    )

    return result


# =============================================================================
# 選択文字列解析
# =============================================================================

def parse_selection(
    text,
    max_index
):
    """
    以下の形式を解析する。

        1
        1,3,5
        1-10
        1,3,8-12
    """

    selected = set()

    parts = [
        part.strip()
        for part in text.split(",")
        if part.strip()
    ]

    for part in parts:

        # ---------------------------------------------------------------------
        # 範囲
        # ---------------------------------------------------------------------

        range_match = re.fullmatch(
            r"(\d+)\s*-\s*(\d+)",
            part
        )

        if range_match:

            start = int(
                range_match.group(1)
            )

            end = int(
                range_match.group(2)
            )

            if start > end:

                start, end = (
                    end,
                    start
                )

            for number in range(
                start,
                end + 1
            ):

                if (
                    1
                    <= number
                    <= max_index
                ):

                    selected.add(
                        number
                    )

            continue

        # ---------------------------------------------------------------------
        # 単一番号
        # ---------------------------------------------------------------------

        if part.isdigit():

            number = int(
                part
            )

            if (
                1
                <= number
                <= max_index
            ):

                selected.add(
                    number
                )

    return sorted(
        selected
    )


# =============================================================================
# 一括ダウンロード実行
# =============================================================================

def run_batch_download(
    pages
):
    """
    上書き確認後、一括ダウンロードする。
    """

    if not pages:

        return

    print()
    print(
        "=" * 80
    )

    print(
        f"選択ページ: {len(pages)} 件"
    )

    print(
        "=" * 80
    )

    overwrite_choice = input(
        "▶ 既存HTMLを上書きしますか？ (Y/N): "
    ).strip().lower()

    overwrite = (
        overwrite_choice
        in {"y", "yes"}
    )

    delay_text = input(
        f"▶ アクセス間隔秒数 "
        f"(Enter={DEFAULT_DELAY}): "
    ).strip()

    if delay_text:

        try:

            delay = float(
                delay_text
            )

            if delay < 0:

                delay = DEFAULT_DELAY

        except ValueError:

            print(
                "不正な値のため既定値を使用します。"
            )

            delay = DEFAULT_DELAY

    else:

        delay = DEFAULT_DELAY

    download_pages(
        pages,
        overwrite=overwrite,
        delay=delay
    )


# =============================================================================
# Master DB自由検索
# =============================================================================

def select_pages_from_search():
    """
    Master DBを検索してページを選択する。

    言語・カテゴリは任意指定。
    """

    print()
    print(
        "=" * 80
    )

    print(
        "Master Database 検索"
    )

    print(
        "=" * 80
    )

    print()

    language = input(
        "▶ 言語 (空欄=指定なし): "
    ).strip()

    category = input(
        "▶ カテゴリコード "
        "(空欄=指定なし): "
    ).strip()

    query = input(
        "▶ 検索文字列 (空欄=全件): "
    ).strip()

    results = search_master(
        query=query,
        language=language or None,
        category=category or None
    )

    if not results:

        print()
        print(
            "❌ 条件に一致するページがありません。"
        )

        return []

    display_count = min(
        len(results),
        100
    )

    print()
    print(
        f"候補数: {len(results)} 件"
    )

    print()

    for index, page in enumerate(
        results[:display_count],
        1
    ):

        mark = (
            "✓"
            if find_existing_html(page)
            else " "
        )

        print(
            f"[{index:03d}] "
            f"{mark} "
            f"{page['title']} "
            f"[{page['language'] or '-'}] "
            f"[{page['category'] or '-'}]"
        )

    if len(results) > display_count:

        print()

        print(
            f"... 残り "
            f"{len(results) - display_count} 件"
        )

    print()
    print(
        "選択方法:"
    )

    print(
        "  1"
    )

    print(
        "  1,3,8"
    )

    print(
        "  10-20"
    )

    print(
        "  all"
    )

    print(
        "  Enter = 戻る"
    )

    print()

    choice = input(
        "▶ 選択: "
    ).strip()

    if not choice:

        return []

    if choice.casefold() == "all":

        return results

    numbers = parse_selection(
        choice,
        display_count
    )

    return [
        results[number - 1]
        for number in numbers
    ]


# =============================================================================
# 言語一覧
# =============================================================================

def get_available_languages():
    """
    Master DBに存在する言語一覧を返す。
    """

    pages = load_master_database()

    languages = {
        page["language"].strip()
        for page in pages
        if page["language"].strip()
    }

    return sorted(
        languages,
        key=lambda value: value.casefold()
    )


# =============================================================================
# 指定言語のカテゴリ一覧
# =============================================================================

def get_available_categories(
    language
):
    """
    指定した言語に実際に存在するカテゴリだけを返す。
    """

    pages = load_master_database()

    normalized_language = (
        language.strip().casefold()
    )

    categories = set()

    for page in pages:

        if (
            page["language"].strip().casefold()
            != normalized_language
        ):

            continue

        for category in (
            page["category"].split("|")
        ):

            category = category.strip()

            if category:

                categories.add(
                    category
                )

    return sorted(
        categories,
        key=get_category_sort_key
    )


# =============================================================================
# 言語選択
# =============================================================================

def select_language():
    """
    Master DBに存在する言語版を選択する。

    各言語の件数も表示する。
    """

    languages = (
        get_available_languages()
    )

    if not languages:

        print()
        print(
            "❌ Master DBに言語情報がありません。"
        )

        return None

    print()
    print(
        "=" * 80
    )

    print(
        "言語版を選択してください"
    )

    print(
        "=" * 80
    )

    print()

    for index, language in enumerate(
        languages,
        1
    ):

        count = len(
            search_master(
                language=language
            )
        )

        print(
            f"[{index}] "
            f"{language} "
            f"({count}件)"
        )

    print()
    print(
        "[0] 戻る"
    )

    print()

    while True:

        choice = input(
            "▶ 選択: "
        ).strip()

        if choice == "0":

            return None

        if not choice.isdigit():

            print(
                "番号を入力してください。"
            )

            continue

        index = (
            int(choice) - 1
        )

        if (
            0
            <= index
            < len(languages)
        ):

            return languages[index]

        print(
            "範囲外の番号です。"
        )


# =============================================================================
# カテゴリ選択
# =============================================================================

def select_category(
    language
):
    """
    選択された言語に実在するカテゴリだけを
    番号選択で表示する。
    """

    categories = (
        get_available_categories(
            language
        )
    )

    if not categories:

        print()
        print(
            f"❌ {language} にカテゴリがありません。"
        )

        return None

    print()
    print(
        "=" * 80
    )

    print(
        f"{language} のカテゴリを選択してください"
    )

    print(
        "=" * 80
    )

    print()

    for index, category in enumerate(
        categories,
        1
    ):

        count = len(
            search_master(
                language=language,
                category=category
            )
        )

        display_name = (
            get_category_display_name(
                language,
                category
            )
        )

        print(
            f"[{index}] "
            f"{display_name} "
            f"({count}件)"
        )

    print()
    print(
        "[0] 戻る"
    )

    print()

    while True:

        choice = input(
            "▶ 選択: "
        ).strip()

        if choice == "0":

            return None

        if not choice.isdigit():

            print(
                "番号を入力してください。"
            )

            continue

        index = (
            int(choice) - 1
        )

        if (
            0
            <= index
            < len(categories)
        ):

            return categories[index]

        print(
            "範囲外の番号です。"
        )


# =============================================================================
# 言語・カテゴリ選択式ダウンロード
# =============================================================================

def filtered_download():
    """
    言語版 → 実在カテゴリ → ページ一覧
    の順に番号選択してダウンロードする。

    [4] classified_download() と違い、
    normal / normal_eta でも一覧から選択する。
    """

    # -------------------------------------------------------------------------
    # 1. 言語
    # -------------------------------------------------------------------------

    language = select_language()

    if language is None:

        return

    # -------------------------------------------------------------------------
    # 2. カテゴリ
    # -------------------------------------------------------------------------

    category = select_category(
        language
    )

    if category is None:

        return

    # -------------------------------------------------------------------------
    # 3. ページ取得
    # -------------------------------------------------------------------------

    pages = search_master(
        language=language,
        category=category
    )

    if not pages:

        print()
        print(
            "❌ 該当ページがありません。"
        )

        return

    display_name = (
        get_category_display_name(
            language,
            category
        )
    )

    print()
    print(
        "=" * 80
    )

    print(
        display_name
    )

    print(
        "=" * 80
    )

    print()
    print(
        f"該当ページ: "
        f"{len(pages)} 件"
    )

    # -------------------------------------------------------------------------
    # 一覧表示
    # -------------------------------------------------------------------------

    display_count = min(
        len(pages),
        100
    )

    print()

    for index, page in enumerate(
        pages[:display_count],
        1
    ):

        mark = (
            "✓"
            if find_existing_html(page)
            else " "
        )

        print(
            f"[{index:03d}] "
            f"{mark} "
            f"{page['title']}"
        )

    if len(pages) > display_count:

        print()
        print(
            f"... 残り "
            f"{len(pages) - display_count} 件"
        )

    print()

    # -------------------------------------------------------------------------
    # 選択
    # -------------------------------------------------------------------------

    print(
        "選択方法:"
    )

    print(
        "  1"
    )

    print(
        "  1,3,5"
    )

    print(
        "  1-20"
    )

    print(
        "  1,3,8-12"
    )

    print(
        "  all"
    )

    print(
        "  Enter = 戻る"
    )

    print()

    choice = input(
        "▶ 選択: "
    ).strip()

    if not choice:

        return

    if choice.casefold() == "all":

        selected = pages

    else:

        numbers = parse_selection(
            choice,
            display_count
        )

        selected = [
            pages[number - 1]
            for number in numbers
        ]

    if not selected:

        print()
        print(
            "選択されたページがありません。"
        )

        return

    # -------------------------------------------------------------------------
    # ダウンロード
    # -------------------------------------------------------------------------

    run_batch_download(
        selected
    )


# =============================================================================
# URL直接指定
# =============================================================================

def make_page_from_url(
    url
):
    """
    URLから一時的なページ情報を作成する。
    """

    url = url.strip()

    if not url:

        return None

    parsed = urlparse(
        url
    )

    title = unquote(
        parsed.path.rstrip("/").split(
            "/"
        )[-1]
    )

    title = title.replace(
        "_",
        " "
    )

    return {
        "title": title,
        "url": url,
        "category": "",
        "language": "",
        "source_type": "direct",
        "indexes": "",
        "translation_section": "",
        "section_number": "",
    }


def direct_url_download():
    """
    URLを直接指定する。
    """

    print()
    print(
        "=" * 80
    )

    print(
        "URL直接ダウンロード"
    )

    print(
        "=" * 80
    )

    print()

    url = input(
        "▶ URL: "
    ).strip()

    if not url:

        return

    page = make_page_from_url(
        url
    )

    if page is None:

        return

    run_batch_download(
        [page]
    )


# =============================================================================
# 通常階層系の整数Level番号抽出
# =============================================================================

def extract_integer_level(
    title,
    category
):
    """
    通常階層 / 通常階層ηから
    整数Level番号だけを抽出する。

    normal:
        Level 0
        Level 178
        Level -10

        Level PL-0
        Level PL-10
        Level ZH 0
        Level ZH 10

    normal_eta:
        Level 0 η
        Level 178 η
        Level -10 η

    非対応:
        Level 178.1
        Level 178.5 η
        Level UA-(-0)
        Level UA-9¾
        Level !
        The Void

    特殊ページはMaster DB上のURLを直接利用すれば
    通常どおりダウンロードできる。
    """

    title = title.strip()

    # -------------------------------------------------------------------------
    # 通常階層
    # -------------------------------------------------------------------------

    if category == "normal":

        patterns = [
            # 通常
            r"^Level\s+(-?\d+)$",

            # PL-0 / PL-10
            r"^Level\s+[A-Z]{2}-(-?\d+)$",

            # ZH 0 / ZH 10
            r"^Level\s+[A-Z]{2}\s+(-?\d+)$",
        ]

    # -------------------------------------------------------------------------
    # 通常階層 η
    # -------------------------------------------------------------------------

    elif category == "normal_eta":

        patterns = [
            r"^Level\s+(-?\d+)\s+η$",
        ]

    else:

        return None

    for pattern in patterns:

        match = re.fullmatch(
            pattern,
            title
        )

        if not match:
            continue

        try:

            return int(
                match.group(1)
            )

        except ValueError:

            return None

    return None


# =============================================================================
# 通常階層系の整数範囲ダウンロード
# =============================================================================

def download_integer_range(
    language,
    category
):
    """
    通常階層 / 通常階層ηの整数番号範囲を
    Master DBから抽出してダウンロードする。

    大量ページを一覧表示せず、
    開始番号・終了番号だけで対象を絞る。

    例:

        400
        600

    → Master DB内の
       Level 400 ～ Level 600
       に該当するページだけを対象にする。
    """

    pages = search_master(
        language=language,
        category=category
    )

    numeric_pages = []

    for page in pages:

        number = extract_integer_level(
            page["title"],
            category
        )

        if number is None:

            continue

        page_copy = dict(
            page
        )

        page_copy[
            "_numeric_level"
        ] = number

        numeric_pages.append(
            page_copy
        )

    if not numeric_pages:

        print()
        print(
            "整数番号として扱える通常階層がありません。"
        )

        return

    # -------------------------------------------------------------------------
    # 数字順
    # -------------------------------------------------------------------------

    numeric_pages.sort(
        key=lambda page: (
            page["_numeric_level"],
            page["title"].casefold(),
            page["url"]
        )
    )

    # -------------------------------------------------------------------------
    # 表示
    # -------------------------------------------------------------------------

    print()
    print(
        "=" * 80
    )

    print(
        get_category_display_name(
            language,
            category
        )
    )

    print(
        "=" * 80
    )

    print()

    print(
        f"整数番号で扱えるページ: "
        f"{len(numeric_pages)} 件"
    )

    print()

    # -------------------------------------------------------------------------
    # 開始番号
    # -------------------------------------------------------------------------

    while True:

        start_text = input(
            "▶ 開始番号: "
        ).strip()

        if not start_text:

            print(
                "キャンセルしました。"
            )

            return

        try:

            start = int(
                start_text
            )

            break

        except ValueError:

            print(
                "整数を入力してください。"
            )

    # -------------------------------------------------------------------------
    # 終了番号
    # -------------------------------------------------------------------------

    while True:

        end_text = input(
            "▶ 終了番号: "
        ).strip()

        if not end_text:

            print(
                "キャンセルしました。"
            )

            return

        try:

            end = int(
                end_text
            )

            break

        except ValueError:

            print(
                "整数を入力してください。"
            )

    # -------------------------------------------------------------------------
    # 範囲反転
    # -------------------------------------------------------------------------

    if start > end:

        print()
        print(
            "[注意] 開始番号が終了番号より大きいため、"
            "順序を入れ替えます。"
        )

        start, end = (
            end,
            start
        )

    # -------------------------------------------------------------------------
    # 対象抽出
    # -------------------------------------------------------------------------

    selected = [
        page
        for page in numeric_pages
        if (
            start
            <= page["_numeric_level"]
            <= end
        )
    ]

    # -------------------------------------------------------------------------
    # 内部情報削除
    # -------------------------------------------------------------------------

    for page in selected:

        page.pop(
            "_numeric_level",
            None
        )

    # -------------------------------------------------------------------------
    # 結果表示
    # -------------------------------------------------------------------------

    print()
    print(
        "=" * 80
    )

    print(
        f"指定範囲: "
        f"{start} ～ {end}"
    )

    print(
        f"該当ページ: "
        f"{len(selected)} 件"
    )

    print(
        "=" * 80
    )

    if not selected:

        print()
        print(
            "該当するページがありません。"
        )

        return

    # -------------------------------------------------------------------------
    # 取得済み / 未取得
    # -------------------------------------------------------------------------

    existing_count = sum(
        1
        for page in selected
        if find_existing_html(page)
    )

    missing_count = (
        len(selected)
        - existing_count
    )

    print()

    print(
        f"取得済み: "
        f"{existing_count} 件"
    )

    print(
        f"未取得  : "
        f"{missing_count} 件"
    )

    # -------------------------------------------------------------------------
    # 最低限のプレビュー
    # -------------------------------------------------------------------------

    preview_count = min(
        len(selected),
        20
    )

    print()

    print(
        "対象プレビュー:"
    )

    for page in selected[
        :preview_count
    ]:

        mark = (
            "✓"
            if find_existing_html(page)
            else " "
        )

        print(
            f" {mark} "
            f"{page['title']}"
        )

    if len(selected) > preview_count:

        print(
            f" ... 他 "
            f"{len(selected) - preview_count} 件"
        )

    # -------------------------------------------------------------------------
    # 確認
    # -------------------------------------------------------------------------

    print()

    confirm = input(
        "▶ この範囲をダウンロードしますか？ "
        "(Y/N): "
    ).strip().lower()

    if confirm not in {
        "y",
        "yes"
    }:

        print(
            "キャンセルしました。"
        )

        return

    # -------------------------------------------------------------------------
    # ダウンロード
    # -------------------------------------------------------------------------

    run_batch_download(
        selected
    )


# =============================================================================
# 非数値カテゴリの一覧選択
# =============================================================================

def download_category_list(
    language,
    category
):
    """
    亜階層・番外階層など、
    数値範囲に向かないカテゴリを一覧選択する。
    """

    pages = search_master(
        language=language,
        category=category
    )

    if not pages:

        print()
        print(
            "該当ページがありません。"
        )

        return

    print()
    print(
        "=" * 80
    )

    print(
        get_category_display_name(
            language,
            category
        )
    )

    print(
        "=" * 80
    )

    print()

    print(
        f"該当ページ: "
        f"{len(pages)} 件"
    )

    display_count = min(
        len(pages),
        100
    )

    print()

    for index, page in enumerate(
        pages[:display_count],
        1
    ):

        mark = (
            "✓"
            if find_existing_html(page)
            else " "
        )

        print(
            f"[{index:03d}] "
            f"{mark} "
            f"{page['title']}"
        )

    if len(pages) > display_count:

        print()

        print(
            f"... 残り "
            f"{len(pages) - display_count} 件"
        )

    print()

    print(
        "選択:"
    )

    print(
        "  1,3,5"
    )

    print(
        "  1-20"
    )

    print(
        "  1,3,8-12"
    )

    print(
        "  all"
    )

    print(
        "  Enter = 戻る"
    )

    print()

    choice = input(
        "▶ 選択: "
    ).strip()

    if not choice:

        return

    if choice.casefold() == "all":

        selected = pages

    else:

        numbers = parse_selection(
            choice,
            display_count
        )

        selected = [
            pages[number - 1]
            for number in numbers
        ]

    if not selected:

        print(
            "選択されたページがありません。"
        )

        return

    run_batch_download(
        selected
    )


# =============================================================================
# 言語版 → カテゴリ → ダウンロード方式
# =============================================================================

def classified_download():
    """
    言語版 → 実在カテゴリ → ダウンロード方式。

    normal:
        整数番号範囲指定

    normal_eta:
        整数番号範囲指定

    その他:
        DB一覧から選択
    """

    while True:

        # ---------------------------------------------------------------------
        # 1. 言語
        # ---------------------------------------------------------------------

        language = select_language()

        if language is None:

            return

        # ---------------------------------------------------------------------
        # 2. カテゴリ
        # ---------------------------------------------------------------------

        category = select_category(
            language
        )

        if category is None:

            continue

        # ---------------------------------------------------------------------
        # 3. カテゴリに応じた処理
        # ---------------------------------------------------------------------

        if category in {
            "normal",
            "normal_eta"
        }:

            download_integer_range(
                language,
                category
            )

        else:

            download_category_list(
                language,
                category
            )

        # ---------------------------------------------------------------------
        # 4. 同じ言語で続けるか
        # ---------------------------------------------------------------------

        print()

        print(
            f"現在の言語版: {language}"
        )

        print()

        again = input(
            "▶ 同じ言語版で別カテゴリを選びますか？ "
            "(Y/N): "
        ).strip().lower()

        if again not in {
            "y",
            "yes"
        }:

            return


# =============================================================================
# 旧API互換
# =============================================================================

def download_levels(
    start_level,
    end_level
):
    """
    旧Versionとの互換用。

    既存コードから、

        download_levels(start_level, end_level)

    と呼び出された場合も動作する。

    Master DB上の
        Level 数値 η

    の実在ページだけを対象にする。
    """

    eta_pages = []

    pages = load_master_database()

    for page in pages:

        title = page.get(
            "title",
            ""
        )

        match = re.fullmatch(
            r"Level\s+"
            r"(-?\d+(?:\.\d+)?)"
            r"\s+η"
            r"(?:\s+\(\d+\))?",
            title
        )

        if not match:

            continue

        try:

            number = float(
                match.group(1)
            )

        except ValueError:

            continue

        if (
            start_level
            <= number
            <= end_level
        ):

            eta_pages.append(
                page
            )

    eta_pages.sort(
        key=lambda page: (
            extract_integer_level(
                page["title"],
                "normal_eta"
            )
            if extract_integer_level(
                page["title"],
                "normal_eta"
            ) is not None
            else 0,
            page["title"].casefold(),
            page["url"]
        )
    )

    print()
    print(
        "=" * 80
    )

    print(
        "Master DB η範囲ダウンロード"
    )

    print(
        f"Level {start_level} η"
        f" ～ "
        f"Level {end_level} η"
    )

    print(
        f"対象: {len(eta_pages)} 件"
    )

    print(
        "=" * 80
    )

    if not eta_pages:

        print(
            "該当するη階層がありません。"
        )

        return

    download_pages(
        eta_pages,
        overwrite=False,
        delay=DEFAULT_DELAY
    )


# =============================================================================
# メインメニュー
# =============================================================================

def show_menu():
    """
    Downloaderメニューを表示する。
    """

    print()
    print(
        "=" * 80
    )

    print(
        " Backrooms Fandom JP - HTML Downloader"
    )

    print(
        "=" * 80
    )

    print()

    print(
        "[1] Master DBから検索・選択してダウンロード"
    )

    print(
        "[2] 言語・カテゴリから選択してダウンロード"
    )

    print(
        "[3] URLを直接指定してダウンロード"
    )

    print(
        "[4] 言語版 → カテゴリ → 範囲/一覧選択"
    )

    print(
        "[0] 終了"
    )

    print()

    print(
        "=" * 80
    )


# =============================================================================
# メイン
# =============================================================================

def main():
    """
    Downloader本体。

    BackroomsNavigationSystem_FandomJP.pyから

        from modules.downloader import main

    として呼び出せる。
    """

    while True:

        show_menu()

        try:

            choice = input(
                "▶選択: "
            ).strip()

        except (
            EOFError,
            KeyboardInterrupt
        ):

            print()
            print(
                "HTML Downloaderを終了します。"
            )

            return

        # ---------------------------------------------------------------------
        # [1] Master DB検索
        # ---------------------------------------------------------------------

        if choice == "1":

            selected = (
                select_pages_from_search()
            )

            if selected:

                run_batch_download(
                    selected
                )

        # ---------------------------------------------------------------------
        # [2] 言語 → 実在カテゴリ → 一覧選択
        # ---------------------------------------------------------------------

        elif choice == "2":

            filtered_download()

        # ---------------------------------------------------------------------
        # [3] URL直接
        # ---------------------------------------------------------------------

        elif choice == "3":

            direct_url_download()

        # ---------------------------------------------------------------------
        # [4] 言語 → 実在カテゴリ → 範囲/一覧
        # ---------------------------------------------------------------------

        elif choice == "4":

            classified_download()

        # ---------------------------------------------------------------------
        # [0] 終了
        # ---------------------------------------------------------------------

        elif choice == "0":

            print()
            print(
                "HTML Downloaderを終了します。"
            )

            return

        # ---------------------------------------------------------------------
        # 無効入力
        # ---------------------------------------------------------------------

        else:

            print()
            print(
                "1 / 2 / 3 / 4 / 0 "
                "のいずれかを入力してください。"
            )


# =============================================================================
# エントリーポイント
# =============================================================================

if __name__ == "__main__":

    main()