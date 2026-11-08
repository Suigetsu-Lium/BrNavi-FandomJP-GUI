# modules/extractor.py

import re
import csv
import html
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup


# ============================================================
# Backrooms Fandom JP - Extractor
#
# 仕様:
#   - 範囲指定なし
#   - 常にALL抽出
#   - 通常Level
#   - η階層
#   - 番外階層
#   - 亜階層
#   - 特殊階層
#   - 画像・写真ページ等は除外
#
# 更新時:
#   1. 個別TXTを全削除
#   2. 全HTMLを再抽出
#   3. Summary CSVを完全再生成
#   4. extracted_fandom_levels_all.txtを完全再生成
# ============================================================


# ============================================================
# 基本パス
# ============================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
    .parent
)

DOWNLOADED_HTML_DIR = (
    BASE_DIR
    / "downloaded_fandom_html"
)

OUTPUT_DIR = (
    BASE_DIR
    / "extracted_fandom_levels"
)

COMBINED_TXT = (
    BASE_DIR
    / "extracted_fandom_levels_all.txt"
)

SUMMARY_CSV = (
    BASE_DIR
    / "extracted_fandom_levels_summary.csv"
)


# ============================================================
# CSVヘッダー
# ============================================================

CSV_HEADERS = [
    "階層コード",
    "メタタイトル",
    "危険度",
    "危険度検索値",
    "理解度",
    "理解度検索値",
    "タグ",
    "URL",
    "カテゴリ",
    "言語",
    "ソース",
    "インデックス",
]


# ============================================================
# 共通
# ============================================================

def clean_text(text):
    """
    文字列を正規化する。
    """

    if text is None:
        return ""

    text = html.unescape(
        str(text)
    )

    # NBSP
    text = text.replace(
        "\xa0",
        " "
    )

    # ゼロ幅文字
    text = text.replace(
        "\u200b",
        ""
    )

    text = text.replace(
        "\u200c",
        ""
    )

    text = text.replace(
        "\u200d",
        ""
    )

    text = text.replace(
        "\ufeff",
        ""
    )

    # 改行
    text = text.replace(
        "\r\n",
        "\n"
    )

    text = text.replace(
        "\r",
        "\n"
    )

    # タブ
    text = text.replace(
        "\t",
        " "
    )

    # 連続スペース
    text = re.sub(
        r"[ ]{2,}",
        " ",
        text
    )

    # 行末空白
    text = re.sub(
        r"[ \t]+$",
        "",
        text,
        flags=re.MULTILINE
    )

    # 連続空行
    text = re.sub(
        r"\n[ \t]*\n[ \t]*\n+",
        "\n\n",
        text
    )

    return text.strip()


def normalize_inline_text(text):
    """
    インラインテキスト用の正規化。
    """

    if text is None:
        return ""

    text = html.unescape(
        str(text)
    )

    text = text.replace(
        "\xa0",
        " "
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def strip_html(text):
    """
    HTMLタグを除去する。
    """

    if not text:
        return ""

    text = re.sub(
        r"<br\s*/?>",
        "\n",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"</p\s*>",
        "\n",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"</div\s*>",
        "\n",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"<[^>]+>",
        "",
        text
    )

    return clean_text(
        text
    )


# ============================================================
# Windowsファイル名
# ============================================================

def safe_filename(filename):
    """
    Windowsで使用できない文字を置換する。

    Levelタイトル・ページタイトルをベースにする。
    """

    if not filename:
        filename = "unknown_level"

    filename = clean_text(
        filename
    )

    # Windows禁止文字
    filename = re.sub(
        r'[<>:"/\\|?*]',
        "_",
        filename
    )

    # 制御文字
    filename = re.sub(
        r"[\x00-\x1f]",
        "_",
        filename
    )

    # 末尾の空白・ピリオド
    filename = filename.rstrip(
        " ."
    )

    if not filename:
        filename = "unknown_level"

    return filename


# ============================================================
# ファイル名からLevelコード
# ============================================================

def level_code_from_filename(
    filename
):
    """
    HTMLファイル名からLevelコードを推測する。

    本文・ページタイトルから取得できる場合は
    そちらを優先する。
    """

    if not filename:
        return ""

    name = Path(
        filename
    ).stem

    name = unquote(
        name
    )

    name = name.replace(
        "_",
        " "
    )

    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()

    # Fandomサイトタイトル部分
    name = re.sub(
        r"\s*[-|]\s*Backrooms Wiki.*$",
        "",
        name,
        flags=re.IGNORECASE
    )

    return normalize_level_code(
        name
    )


# ============================================================
# 階層タイトルの除外判定
# ============================================================

IMAGE_PAGE_PATTERNS = [
    r"写真",
    r"画像",
    r"撮影された",
    r"撮影した写真",
    r"映した写真",
    r"写した写真",
    r"内部の写真",
    r"内の写真",
    r"の写真",
]


def is_probable_non_level_page_title(
    title
):
    """
    明らかな画像・写真・特殊ページを除外する。

    番外・特殊階層は除外しない。
    """

    if not title:
        return True

    title = clean_text(
        title
    )

    if not title:
        return True

    # --------------------------------------------------------
    # 画像・写真ページ
    # --------------------------------------------------------

    for pattern in IMAGE_PAGE_PATTERNS:

        if re.search(
            pattern,
            title,
            flags=re.IGNORECASE
        ):

            return True

    # --------------------------------------------------------
    # Fandom特殊ページ
    # --------------------------------------------------------

    excluded_prefixes = (
        "File:",
        "ファイル:",
        "Category:",
        "カテゴリ:",
        "Template:",
        "テンプレート:",
    )

    for prefix in excluded_prefixes:

        if title.startswith(
            prefix
        ):

            return True

    # --------------------------------------------------------
    # 明らかなUI
    # --------------------------------------------------------

    excluded_exact = {
        "ホーム",
        "探索",
        "メインページ",
        "ディスカッション",
        "全ページ",
        "コミュニティ",
        "カテゴリ",
        "Backrooms Wiki",
    }

    if title in excluded_exact:
        return True

    return False


# ============================================================
# 本文行が正式Levelタイトルか
# ============================================================

def is_level_title_line(
    line
):
    """
    本文中の行が正式なLevelタイトルか判定する。

    採用例:
        Level 459 η: "無銘路"
        Level 108 η: "The front of Ryuuguu"
        Level 000 η
        Level 0.01 η
        Level -1 η
        Level !
        Level ! (1)
        Level Snow Globe H η

    除外例:
        Level 0 η の写真
        Level 1 η の写真
        Level 0.01 η を映した写真。
        Level -1.5 η の地下 1 階
        Level 0 η にて落ちていた...
    """

    if not line:
        return False

    line = clean_text(
        line
    )

    if not line:
        return False

    # --------------------------------------------------------
    # Levelで始まる必要がある
    # --------------------------------------------------------

    if not re.match(
        r"^Level(?:\s|$)",
        line,
        flags=re.IGNORECASE
    ):
        return False

    # --------------------------------------------------------
    # Fandomタイトル等
    # --------------------------------------------------------

    if "|" in line:
        return False

    if re.search(
        r"Backrooms\s+Wiki",
        line,
        flags=re.IGNORECASE
    ):
        return False

    # --------------------------------------------------------
    # 明らかな画像・写真ページ
    # --------------------------------------------------------

    if is_probable_non_level_page_title(
        line
    ):
        return False

    # ========================================================
    # 1. Levelコード: メタタイトル
    # ========================================================

    if re.fullmatch(
        r"Level\s+.+?\s*[:：]\s*.+",
        line,
        flags=re.IGNORECASE
    ):
        return True

    # ========================================================
    # 2. 数値Level
    # ========================================================

    if re.fullmatch(
        r"Level\s+-?\d+(?:\.\d+)?",
        line,
        flags=re.IGNORECASE
    ):
        return True

    # ========================================================
    # 3. 数値Level η
    # ========================================================

    if re.fullmatch(
        r"Level\s+-?\d+(?:\.\d+)?\s+η",
        line,
        flags=re.IGNORECASE
    ):
        return True

    # ========================================================
    # 4. Level !
    # ========================================================

    if re.fullmatch(
        r"Level\s+!\s*(?:\(\d+\))?",
        line,
        flags=re.IGNORECASE
    ):
        return True

    # ========================================================
    # 5. 特殊Levelコード + η
    #
    # 例:
    #   Level √-7 η
    #   Level ⊗ η
    #   Level Snow Globe H η
    # ========================================================

    if re.fullmatch(
        r"Level\s+.+?\s+η",
        line,
        flags=re.IGNORECASE
    ):
        return True

    return False


# ============================================================
# 本文から正式Levelタイトルを探す
# ============================================================

def find_level_title_line(
    article_text
):
    """
    本文中から最初の正式なLevelタイトル行を探す。

    先頭に、

        The Backrooms
        導入文

    が存在していても問題ない。
    """

    if not article_text:
        return ""

    for line in article_text.splitlines():

        line = clean_text(
            line
        )

        if not line:
            continue

        if is_level_title_line(
            line
        ):
            return line

    return ""


# ============================================================
# HTMLから実際のページタイトル
# ============================================================

def extract_page_title_from_html(
    html_text
):
    """
    Fandom HTMLから実際のページタイトルを取得する。

    優先:
        1. .mw-page-title-main
        2. #firstHeading
        3. h1
    """

    if not html_text:
        return ""

    soup = BeautifulSoup(
        html_text,
        "html.parser"
    )

    # --------------------------------------------------------
    # mw-page-title-main
    # --------------------------------------------------------

    element = soup.select_one(
        ".mw-page-title-main"
    )

    if element:

        title = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    # --------------------------------------------------------
    # firstHeading
    # --------------------------------------------------------

    element = soup.find(
        id="firstHeading"
    )

    if element:

        title = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    # --------------------------------------------------------
    # h1
    # --------------------------------------------------------

    element = soup.find(
        "h1"
    )

    if element:

        title = clean_text(
            element.get_text(
                " ",
                strip=True
            )
        )

        if title:
            return title

    return ""


# ============================================================
# Levelコード
# ============================================================

def normalize_level_code(
    level_code
):
    """
    階層コードを軽く正規化する。
    """

    if not level_code:
        return ""

    level_code = clean_text(
        level_code
    )

    level_code = re.sub(
        r"^Level\s+",
        "Level ",
        level_code,
        flags=re.IGNORECASE
    )

    return level_code


def extract_level_code_from_title(
    title_line
):
    """
    Levelタイトル行から階層コードを抽出。

    例:
        Level 459 η: "無銘路"

    → Level 459 η
    """

    if not title_line:
        return ""

    title_line = clean_text(
        title_line
    )

    match = re.match(
        r"^(Level\s+[^:：]+)",
        title_line,
        flags=re.IGNORECASE
    )

    if match:

        return normalize_level_code(
            match.group(1)
        )

    return normalize_level_code(
        title_line
    )


# ============================================================
# メタタイトル
# ============================================================

def extract_meta_title_from_level_title(
    title_line
):
    """
    Levelタイトル行からメタタイトルを取得。

    例:
        Level 459 η: "無銘路"

    → 無銘路
    """

    if not title_line:
        return ""

    title_line = clean_text(
        title_line
    )

    if "：" in title_line:

        _, value = title_line.split(
            "：",
            1
        )

    elif ":" in title_line:

        _, value = title_line.split(
            ":",
            1
        )

    else:

        return ""

    value = value.strip()

    if not value:
        return ""

    # 外側の引用符のみ除去
    quote_pairs = [
        ('"', '"'),
        ("「", "」"),
        ("『", "』"),
        ("“", "”"),
        ("‘", "’"),
    ]

    for left_quote, right_quote in quote_pairs:

        if (
            len(value) >= 2
            and value.startswith(
                left_quote
            )
            and value.endswith(
                right_quote
            )
        ):

            value = value[
                len(left_quote):
                -len(right_quote)
            ].strip()

            break

    return value


# ============================================================
# HTML管理情報
# ============================================================

def extract_meta_info_from_html(
    html_text
):
    """
    Downloaderが埋め込んだ
    BRNAVI_META_INFOを取得する。
    """

    result = {
        "title": "",
        "url": "",
        "category": "",
        "language": "",
        "source_type": "",
        "indexes": "",
    }

    if not html_text:
        return result

    match = re.search(
        r"<!--\s*BRNAVI_META_INFO:\s*(.*?)-->",
        html_text,
        flags=re.IGNORECASE | re.DOTALL
    )

    if not match:
        return result

    content = (
        match.group(1)
        .strip()
    )

    for part in content.split("|"):

        part = part.strip()

        if "=" not in part:
            continue

        key, value = part.split(
            "=",
            1
        )

        key = key.strip()
        value = value.strip()

        if key == "TITLE":

            result["title"] = value

        elif key == "URL":

            result["url"] = value

        elif key == "CATEGORY":

            result["category"] = value

        elif key == "LANGUAGE":

            result["language"] = value

        elif key == "SOURCE":

            result["source_type"] = value

        elif key == "INDEXES":

            result["indexes"] = value

    return result


# ============================================================
# URL
# ============================================================

def extract_url_from_html(
    html_text
):
    """
    canonical URL / og:urlを取得する。
    """

    if not html_text:
        return ""

    patterns = [

        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](.*?)["\']',

        r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\'](.*?)["\']',

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            html_text,
            flags=re.IGNORECASE | re.DOTALL
        )

        if match:

            return html.unescape(
                match.group(1).strip()
            )

    return ""


def build_fandom_url(
    level_code
):
    """
    LevelコードからFandom JP URLを作る。
    """

    if not level_code:
        return ""

    title = level_code.replace(
        " ",
        "_"
    )

    return (
        "https://backrooms.fandom.com/ja/wiki/"
        + title
    )


# ============================================================
# 記事本文準備
# ============================================================

def prepare_content_element(
    soup
):
    """
    Fandom記事本文領域を取得する。
    """

    content = soup.find(
        class_="mw-parser-output"
    )

    if not content:

        content = soup.find(
            id="mw-content-text"
        )

    if not content:

        content = soup.find(
            "article"
        )

    if not content:

        content = soup.body

    if not content:
        return None

    # --------------------------------------------------------
    # 不要class
    # --------------------------------------------------------

    unwanted_classes = [
        "toc",
        "navbox",
        "reflink",
        "mw-editsection",
        "reference",
        "references",
        "boilerplate",
        "wds-tabs",
        "thumbcaption",
        "gallerybox",
        "printfooter",
    ]

    for element in content.find_all(
        class_=unwanted_classes
    ):

        element.decompose()

    # --------------------------------------------------------
    # 不要タグ
    # --------------------------------------------------------

    for element in content.find_all(
        [
            "script",
            "style",
            "noscript",
        ]
    ):

        element.decompose()

    return content


# ============================================================
# 本文抽出
# ============================================================

def extract_article_text(
    html_text
):
    """
    Fandom HTMLから記事本文をTXTへ変換する。

    HTML全体ではなく、可能な限り
    mw-parser-outputを使用する。

    見出し:
        概要
    →
        [概要]

    段落:
        空行で区切る。
    """

    if not html_text:
        return ""

    soup = BeautifulSoup(
        html_text,
        "html.parser"
    )

    content = prepare_content_element(
        soup
    )

    if not content:
        return ""

    # --------------------------------------------------------
    # 見出し
    # --------------------------------------------------------

    for heading in content.find_all(
        [
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        ]
    ):

        headline = heading.find(
            class_="mw-headline"
        )

        if headline:

            text = normalize_inline_text(
                headline.get_text(
                    " ",
                    strip=True
                )
            )

        else:

            text = normalize_inline_text(
                heading.get_text(
                    " ",
                    strip=True
                )
            )

        heading.clear()

        if text:

            heading.append(
                f"[{text}]"
            )

    # --------------------------------------------------------
    # br
    # --------------------------------------------------------

    for br in content.find_all(
        "br"
    ):

        br.replace_with(
            "\n"
        )

    # --------------------------------------------------------
    # table cell
    # --------------------------------------------------------

    for cell in content.find_all(
        [
            "td",
            "th",
        ]
    ):

        cell.append(
            " "
        )

    # --------------------------------------------------------
    # block要素
    # --------------------------------------------------------

    block_tags = [
        "p",
        "div",
        "li",
        "tr",
        "blockquote",
        "table",
        "figure",
        "figcaption",
    ]

    for element in content.find_all(
        block_tags
    ):

        element.append(
            "\n"
        )

    # --------------------------------------------------------
    # TXT化
    # --------------------------------------------------------

    raw_text = content.get_text(
        separator=""
    )

    raw_text = html.unescape(
        raw_text
    )

    lines = []

    for line in raw_text.splitlines():

        line = clean_text(
            line
        )

        if not line:
            continue

        if line in (
            "[]",
            "[編集]",
        ):
            continue

        lines.append(
            line
        )

    # --------------------------------------------------------
    # 連続した完全重複行のみ除去
    # --------------------------------------------------------

    cleaned_lines = []

    previous = None

    for line in lines:

        if line == previous:
            continue

        cleaned_lines.append(
            line
        )

        previous = line

    return "\n\n".join(
        cleaned_lines
    ).strip()


# ============================================================
# ページタイトルの決定
# ============================================================

def determine_page_level_title(
    article_text,
    page_title,
    html_filename
):
    """
    最終的に階層名として使用するタイトルを決定。

    優先順位:

        1. 本文中の正式Levelタイトル
        2. Fandomページタイトル
        3. HTMLファイル名

    画像ページなどは除外。
    """

    # --------------------------------------------------------
    # 1. 本文中の正式Levelタイトル
    # --------------------------------------------------------

    level_title = find_level_title_line(
        article_text
    )

    if level_title:
        return level_title

    # --------------------------------------------------------
    # 2. ページタイトル
    # --------------------------------------------------------

    if page_title:

        page_title = clean_text(
            page_title
        )

        if not is_probable_non_level_page_title(
            page_title
        ):

            return page_title

    # --------------------------------------------------------
    # 3. HTMLファイル名
    #
    # ただし、明らかな画像ファイル等は除外
    # --------------------------------------------------------

    fallback = level_code_from_filename(
        html_filename
    )

    if fallback:

        if not is_probable_non_level_page_title(
            fallback
        ):

            return fallback

    return ""


# ============================================================
# 階層情報
# ============================================================

def determine_level_information(
    article_text,
    page_title,
    filename
):
    """
    階層コード・メタタイトルを決定する。

    通常Level:
        Levelタイトル行から取得。

    番外・特殊階層:
        ページタイトルを階層コードとして使用。

    Returns:
        (
            level_title,
            level_code,
            meta_title
        )
    """

    # --------------------------------------------------------
    # 本文の正式Levelタイトル
    # --------------------------------------------------------

    level_title = find_level_title_line(
        article_text
    )

    if level_title:

        level_code = (
            extract_level_code_from_title(
                level_title
            )
        )

        meta_title = (
            extract_meta_title_from_level_title(
                level_title
            )
        )

        return (
            level_title,
            level_code,
            meta_title,
        )

    # --------------------------------------------------------
    # 番外・特殊階層
    # --------------------------------------------------------

    if page_title:

        page_title = clean_text(
            page_title
        )

        if not is_probable_non_level_page_title(
            page_title
        ):

            return (
                page_title,
                page_title,
                "",
            )

    # --------------------------------------------------------
    # ファイル名 fallback
    # --------------------------------------------------------

    filename_title = level_code_from_filename(
        filename
    )

    if filename_title:

        if not is_probable_non_level_page_title(
            filename_title
        ):

            return (
                filename_title,
                filename_title,
                "",
            )

    return (
        "",
        "",
        "",
    )


# ============================================================
# TXT保存
# ============================================================

def save_individual_txt(
    article_text,
    output_dir,
    title_line
):
    """
    個別TXTを保存する。

    正式な階層タイトルがない場合は
    保存しない。
    """

    if not title_line:
        return None

    output_dir = Path(
        output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    filename = (
        safe_filename(
            title_line
        )
        + ".txt"
    )

    output_path = (
        output_dir / filename
    )

    # 更新開始時に既存TXTを削除しているため
    # 単純上書き
    with open(
        output_path,
        "w",
        encoding="utf-8-sig"
    ) as file:

        file.write(
            article_text.rstrip()
            + "\n"
        )

    return output_path


# ============================================================
# 既存TXT削除
# ============================================================

def clear_output_txt_files(
    output_dir
):
    """
    extracted_fandom_levels内の
    既存TXTを全削除する。
    """

    output_dir = Path(
        output_dir
    )

    if not output_dir.exists():
        return 0

    deleted_count = 0

    for path in output_dir.iterdir():

        if not path.is_file():
            continue

        if path.suffix.lower() != ".txt":
            continue

        try:

            path.unlink()

            deleted_count += 1

        except OSError as e:

            print(
                f"⚠ 削除できません: "
                f"{path.name} - {e}"
            )

    return deleted_count


# ============================================================
# 統合TXT
# ============================================================

def rebuild_combined_txt(
    output_dir=OUTPUT_DIR
):
    """
    個別TXTから
    extracted_fandom_levels_all.txtを
    完全再生成する。
    """

    output_dir = Path(
        output_dir
    )

    if not output_dir.exists():

        print(
            "⚠ 個別TXTフォルダがありません。"
        )

        return False

    txt_files = sorted(
        [
            path
            for path in output_dir.iterdir()
            if (
                path.is_file()
                and path.suffix.lower() == ".txt"
            )
        ],
        key=lambda path:
            path.name.casefold()
    )

    try:

        with open(
            COMBINED_TXT,
            "w",
            encoding="utf-8-sig"
        ) as output_file:

            first = True

            for txt_path in txt_files:

                try:

                    with open(
                        txt_path,
                        "r",
                        encoding="utf-8-sig",
                        errors="ignore"
                    ) as input_file:

                        content = (
                            input_file.read()
                            .strip()
                        )

                    if not content:
                        continue

                    if not first:

                        output_file.write(
                            "\n\n"
                        )

                    output_file.write(
                        content
                    )

                    first = False

                except OSError as e:

                    print(
                        f"⚠ 統合スキップ: "
                        f"{txt_path.name} - {e}"
                    )

        print()
        print(
            "統合TXTを再生成しました。"
        )

        print(
            f"  個別TXT : "
            f"{len(txt_files)} 件"
        )

        print(
            f"  統合TXT : "
            f"{COMBINED_TXT}"
        )

        return True

    except OSError as e:

        print(
            "❌ 統合TXTの生成に失敗しました。"
        )

        print(
            f"  {e}"
        )

        return False


# ============================================================
# 危険度
# ============================================================

def extract_danger(
    text
):
    """
    危険度を取得する。
    """

    if not text:
        return "", ""

    patterns = [
        r"危険度\s*[:：]\s*([^\n]+)",
        r"Danger\s*[:：]\s*([^\n]+)",
    ]

    value = ""

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            break

    if not value:
        return "", ""

    value = value.split(
        "\n",
        1
    )[0].strip()

    # --------------------------------------------------------
    # 可変
    # --------------------------------------------------------

    if "可変" in value:

        values = re.findall(
            r"\d+\s*/\s*5",
            text
        )

        values = [
            item.replace(
                " ",
                ""
            )
            for item in values
        ]

        values = list(
            dict.fromkeys(
                values
            )
        )

        if values:

            return (
                "可変",
                "|".join(values)
            )

        return (
            "可変",
            "可変"
        )

    # --------------------------------------------------------
    # N/A
    # --------------------------------------------------------

    if value.upper() == "N/A":

        return (
            "N/A",
            "N/A"
        )

    # --------------------------------------------------------
    # 未定 / 不明
    # --------------------------------------------------------

    if value in (
        "未定",
        "不明"
    ):

        return (
            value,
            value
        )

    # --------------------------------------------------------
    # 通常
    # --------------------------------------------------------

    match = re.search(
        r"\d+\s*/\s*5",
        value
    )

    if match:

        normalized = (
            match.group(0)
            .replace(
                " ",
                ""
            )
        )

        return (
            normalized,
            normalized
        )

    return (
        value,
        value
    )


# ============================================================
# 理解度
# ============================================================

def extract_understanding(
    text
):
    """
    理解度を取得する。
    """

    if not text:
        return "", ""

    patterns = [
        r"理解度\s*[:：]\s*([^\n]+)",
        r"Understanding\s*[:：]\s*([^\n]+)",
    ]

    value = ""

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            value = clean_text(
                match.group(1)
            )

            break

    if not value:
        return "", ""

    value = value.split(
        "\n",
        1
    )[0].strip()

    match = re.search(
        r"(\d+(?:\.\d+)?)\s*%",
        value
    )

    if match:

        number = match.group(1)

        return (
            f"{number}%",
            number
        )

    if value in (
        "不明",
        "未定",
        "N/A"
    ):

        return (
            value,
            ""
        )

    return (
        value,
        value
    )


# ============================================================
# タグ
# ============================================================

def extract_tags(
    text
):
    """
    タグを取得する。
    """

    if not text:
        return ""

    patterns = [
        r"タグ\s*[:：]\s*([^\n]+)",
        r"Tags?\s*[:：]\s*([^\n]+)",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if match:

            return clean_text(
                match.group(1)
            )

    return ""


# ============================================================
# Summary CSV行
# ============================================================

def make_summary_row(
    html_text,
    article_text,
    filename,
    index
):
    """
    Summary CSV用の1行を生成する。
    """

    # --------------------------------------------------------
    # Downloader管理情報
    # --------------------------------------------------------

    html_meta = (
        extract_meta_info_from_html(
            html_text
        )
    )

    # --------------------------------------------------------
    # ページタイトル
    # --------------------------------------------------------

    page_title = (
        extract_page_title_from_html(
            html_text
        )
    )

    # --------------------------------------------------------
    # 階層情報
    # --------------------------------------------------------

    (
        level_title,
        level_code,
        meta_title,
    ) = determine_level_information(
        article_text,
        page_title,
        filename
    )

    # --------------------------------------------------------
    # メタタイトル fallback
    # --------------------------------------------------------

    if not meta_title:

        # 管理情報に明示的タイトルがある場合
        meta_from_downloader = (
            html_meta.get(
                "title",
                ""
            )
        )

        if (
            meta_from_downloader
            and meta_from_downloader
            != level_code
        ):

            meta_title = clean_text(
                meta_from_downloader
            )

    # --------------------------------------------------------
    # 危険度
    # --------------------------------------------------------

    (
        danger_display,
        danger_search
    ) = extract_danger(
        article_text
    )

    # --------------------------------------------------------
    # 理解度
    # --------------------------------------------------------

    (
        understanding_display,
        understanding_search
    ) = extract_understanding(
        article_text
    )

    # --------------------------------------------------------
    # タグ
    # --------------------------------------------------------

    tags = extract_tags(
        article_text
    )

    # --------------------------------------------------------
    # URL
    # --------------------------------------------------------

    url = (
        html_meta.get(
            "url",
            ""
        )
        or extract_url_from_html(
            html_text
        )
    )

    if not url:

        url = build_fandom_url(
            level_code
        )

    # --------------------------------------------------------
    # その他のメタ情報
    # --------------------------------------------------------

    category = (
        html_meta.get(
            "category",
            ""
        )
    )

    language = (
        html_meta.get(
            "language",
            ""
        )
    )

    source = (
        html_meta.get(
            "source_type",
            ""
        )
    )

    indexes = (
        html_meta.get(
            "indexes",
            ""
        )
    )

    return {
        "階層コード": level_code,

        "メタタイトル": meta_title,

        "危険度": danger_display,

        "危険度検索値": danger_search,

        "理解度": understanding_display,

        "理解度検索値": understanding_search,

        "タグ": tags,

        "URL": url,

        "カテゴリ": category,

        "言語": language,

        "ソース": source,

        "インデックス": indexes,
    }


# ============================================================
# Summary CSV完全再生成
# ============================================================

def save_summary_csv(
    rows,
    csv_path=SUMMARY_CSV
):
    """
    Summary CSVを完全再生成する。
    """

    csv_path = Path(
        csv_path
    )

    csv_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        csv_path,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=CSV_HEADERS,
            extrasaction="ignore"
        )

        writer.writeheader()

        for row in rows:

            clean_row = {
                key: row.get(
                    key,
                    ""
                )
                for key in CSV_HEADERS
            }

            writer.writerow(
                clean_row
            )


# ============================================================
# メタタイトル一覧
# ============================================================

def load_meta_titles(
    csv_path=SUMMARY_CSV
):
    """
    CSVから

        階層コード -> メタタイトル

    の辞書を読み込む。
    """

    meta_titles = {}

    csv_path = Path(
        csv_path
    )

    if not csv_path.exists():
        return meta_titles

    try:

        with open(
            csv_path,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(
                file
            )

            for row in reader:

                level_code = clean_text(
                    row.get(
                        "階層コード",
                        ""
                    )
                )

                meta_title = clean_text(
                    row.get(
                        "メタタイトル",
                        ""
                    )
                )

                if level_code:

                    meta_titles[
                        level_code
                    ] = meta_title

    except Exception as e:

        print(
            "メタタイトルCSV読み込みエラー:"
        )

        print(
            f"  {e}"
        )

    return meta_titles


# ============================================================
# Levelガイド
# ============================================================

def extract_level_guide(
    level_code,
    meta_titles=None
):
    """
    Level表示用文字列を生成する。
    """

    level_code = normalize_level_code(
        level_code
    )

    if meta_titles is None:

        meta_titles = (
            load_meta_titles()
        )

    meta_title = meta_titles.get(
        level_code,
        ""
    )

    if meta_title:

        return (
            f"{level_code}: "
            f"{meta_title}"
        )

    return level_code


# ============================================================
# HTML 1件処理
# ============================================================

def process_html_file(
    html_path,
    output_dir,
    index
):
    """
    HTML 1件を処理する。

    通常Level:
        本文内のLevelタイトルを優先。

    番外・特殊階層:
        Fandomのページタイトルを使用。

    写真・画像ページ:
        スキップ。
    """

    html_path = Path(
        html_path
    )

    # --------------------------------------------------------
    # HTML読み込み
    # --------------------------------------------------------

    try:

        with open(
            html_path,
            "r",
            encoding="utf-8",
            errors="ignore"
        ) as file:

            html_text = file.read()

    except OSError as e:

        print(
            f"  ⚠ 読み込み失敗: "
            f"{e}"
        )

        return None

    # --------------------------------------------------------
    # ページタイトル
    # --------------------------------------------------------

    page_title = (
        extract_page_title_from_html(
            html_text
        )
    )

    # --------------------------------------------------------
    # 明らかな非階層ページ
    # --------------------------------------------------------

    if is_probable_non_level_page_title(
        page_title
    ):

        print(
            "  → 画像・写真等の"
            "非階層ページとしてスキップ"
        )

        return None

    # --------------------------------------------------------
    # 本文
    # --------------------------------------------------------

    article_text = (
        extract_article_text(
            html_text
        )
    )

    if not article_text:

        print(
            "  → 本文を取得できないためスキップ"
        )

        return None

    # --------------------------------------------------------
    # 階層タイトル
    # --------------------------------------------------------

    (
        level_title,
        level_code,
        meta_title
    ) = determine_level_information(
        article_text,
        page_title,
        html_path.name
    )

    if not level_title:

        print(
            "  → 正式な階層タイトルを"
            "取得できないためスキップ"
        )

        return None

    # --------------------------------------------------------
    # TXT
    # --------------------------------------------------------

    txt_path = save_individual_txt(
        article_text,
        output_dir,
        level_title
    )

    if txt_path is None:

        return None

    # --------------------------------------------------------
    # CSV行
    # --------------------------------------------------------

    row = make_summary_row(
        html_text,
        article_text,
        html_path.name,
        index
    )

    return row


# ============================================================
# ALL抽出
# ============================================================

def extract_levels(
    html_dir=DOWNLOADED_HTML_DIR,
    output_dir=OUTPUT_DIR,
    summary_csv=SUMMARY_CSV,
    interactive=False
):
    """
    downloaded_fandom_html内のHTMLを
    常にALL処理する。

    範囲指定は使用しない。

    interactive:
        BackroomsNavigationSystem_FandomJP.py
        との互換性のため受け取る。
    """

    html_dir = Path(
        html_dir
    )

    output_dir = Path(
        output_dir
    )

    summary_csv = Path(
        summary_csv
    )

    # ========================================================
    # HTMLフォルダ確認
    # ========================================================

    if not html_dir.exists():

        print()
        print(
            "❌ HTMLフォルダが存在しません。"
        )

        print(
            f"  {html_dir}"
        )

        return []

    # ========================================================
    # 出力フォルダ
    # ========================================================

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    # ========================================================
    # 開始
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "Backrooms Fandom JP - "
        "情報データベース更新"
    )

    print(
        "=" * 80
    )

    print()

    print(
        "処理モード : ALL"
    )

    print()

    # ========================================================
    # 既存TXT削除
    # ========================================================

    deleted_count = (
        clear_output_txt_files(
            output_dir
        )
    )

    print(
        f"既存個別TXT削除 : "
        f"{deleted_count} 件"
    )

    print()

    # ========================================================
    # HTML一覧
    # ========================================================

    html_files = sorted(
        [
            path
            for path in html_dir.iterdir()
            if (
                path.is_file()
                and path.suffix.lower()
                in (
                    ".html",
                    ".htm"
                )
            )
        ],
        key=lambda path:
            path.name.casefold()
    )

    total = len(
        html_files
    )

    print(
        f"処理対象HTML : "
        f"{total} 件"
    )

    print()

    # ========================================================
    # 抽出
    # ========================================================

    rows = []

    success_count = 0

    skipped_count = 0

    error_count = 0

    for index, html_path in enumerate(
        html_files,
        1
    ):

        print(
            f"[{index}/{total}] "
            f"{html_path.name}"
        )

        try:

            row = process_html_file(
                html_path,
                output_dir,
                index
            )

            if row is None:

                skipped_count += 1

                continue

            rows.append(
                row
            )

            success_count += 1

        except Exception as e:

            error_count += 1

            print(
                f"  ❌ エラー: "
                f"{type(e).__name__}: {e}"
            )

    # ========================================================
    # Summary CSV
    # ========================================================

    save_summary_csv(
        rows,
        summary_csv
    )

    # ========================================================
    # 統合TXT
    # ========================================================

    combined_success = (
        rebuild_combined_txt(
            output_dir
        )
    )

    # ========================================================
    # 完了
    # ========================================================

    print()
    print(
        "=" * 80
    )

    print(
        "抽出完了"
    )

    print(
        "=" * 80
    )

    print()

    print(
        f"処理対象HTML : "
        f"{total} 件"
    )

    print(
        f"抽出成功     : "
        f"{success_count} 件"
    )

    print(
        f"対象外       : "
        f"{skipped_count} 件"
    )

    print(
        f"抽出エラー   : "
        f"{error_count} 件"
    )

    print()

    print(
        "出力ファイル"
    )

    print(
        f"  CSV : "
        f"{summary_csv}"
    )

    print(
        f"  TXT : "
        f"{output_dir}"
    )

    print(
        f"  統合TXT : "
        f"{COMBINED_TXT}"
    )

    print()

    if not combined_success:

        print(
            "⚠ 統合TXTの再生成に失敗しました。"
        )

        print()

    return rows


# ============================================================
# 互換用
# ============================================================

def extract_all_levels(
    html_dir=DOWNLOADED_HTML_DIR,
    output_dir=OUTPUT_DIR,
    summary_csv=SUMMARY_CSV
):
    """
    旧コードとの互換用。
    """

    return extract_levels(
        html_dir=html_dir,
        output_dir=output_dir,
        summary_csv=summary_csv,
        interactive=False
    )


# ============================================================
# エントリーポイント
# ============================================================

if __name__ == "__main__":

    extract_levels(
        interactive=True
    )