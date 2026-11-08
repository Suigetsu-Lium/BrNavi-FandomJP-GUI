"""
Backrooms Navigation System - Fandom JP
Level List Generator

pages_master.csv から、
言語別の閲覧・検索用TXTを生成する。

出力例:

level_lists/
├─ JP.txt
├─ EN.txt
├─ FR.txt
├─ PL.txt
├─ UA.txt
└─ ...

各TXTは以下の形式:

    [normal]
    Level 0
    Level 1
    ...

    [normal_eta]
    Level 0 η
    Level 1 η
    ...

重要:
    ページタイトルは正規化しない。
    Fandom上の実際のタイトルをそのまま保存する。
"""


import csv
import os
from collections import defaultdict


# =============================================================================
# 基本パス
# =============================================================================

BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

PARSED_DIR = os.path.join(
    BASE_DIR,
    "parsed_index"
)

INPUT_CSV = os.path.join(
    PARSED_DIR,
    "pages_master.csv"
)

OUTPUT_DIR = os.path.join(
    BASE_DIR,
    "level_lists"
)


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
# CSV読み込み
# =============================================================================

def load_master_csv():

    if not os.path.exists(
        INPUT_CSV
    ):

        print()
        print(
            "【エラー】pages_master.csv が見つかりません。"
        )

        print(
            f"  {INPUT_CSV}"
        )

        return []

    with open(
        INPUT_CSV,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(
            f
        )

        rows = list(
            reader
        )

    return rows


# =============================================================================
# 値の正規化
# =============================================================================

def clean(value):

    if value is None:
        return ""

    return str(value).strip()


# =============================================================================
# 言語列の取得
# =============================================================================

def get_languages(row):

    value = clean(
        row.get("language")
    )

    if not value:
        return ["unknown"]

    # pages_master.csv では念のため | に対応
    languages = []

    for language in value.split("|"):

        language = language.strip()

        if not language:
            continue

        if language not in languages:
            languages.append(
                language
            )

    return languages or ["unknown"]


# =============================================================================
# カテゴリ列の取得
# =============================================================================

def get_categories(row):

    value = clean(
        row.get("category")
    )

    if not value:
        return ["unknown"]

    categories = []

    for category in value.split("|"):

        category = category.strip()

        if not category:
            continue

        if category not in categories:
            categories.append(
                category
            )

    return categories or ["unknown"]


# =============================================================================
# 並び順用キー
# =============================================================================

def category_sort_key(
    category
):

    try:

        order = CATEGORY_ORDER.index(
            category
        )

    except ValueError:

        order = len(
            CATEGORY_ORDER
        )

    return (
        order,
        category
    )


# =============================================================================
# ページ分類
# =============================================================================

def build_language_database(
    rows
):
    """
    言語 → カテゴリ → ページ
    の構造に変換する。

    同一ページが複数カテゴリに所属している場合は、
    各カテゴリに表示する。
    """

    database = defaultdict(
        lambda: defaultdict(list)
    )

    # URL重複防止
    seen = defaultdict(
        lambda: defaultdict(set)
    )

    for row in rows:

        title = clean(
            row.get("title")
        )

        url = clean(
            row.get("url")
        )

        if not title or not url:
            continue

        languages = get_languages(
            row
        )

        categories = get_categories(
            row
        )

        for language in languages:

            for category in categories:

                if url in seen[
                    language
                ][category]:

                    continue

                seen[
                    language
                ][category].add(
                    url
                )

                database[
                    language
                ][category].append({
                    "title": title,
                    "url": url,
                    "source_type": clean(
                        row.get(
                            "source_type"
                        )
                    ),
                    "indexes": clean(
                        row.get(
                            "indexes"
                        )
                    ),
                    "translation_section": clean(
                        row.get(
                            "translation_section"
                        )
                    ),
                })

    return database


# =============================================================================
# ページ並び替え
# =============================================================================

def sort_pages(
    pages
):
    """
    タイトルの文字列順で並べる。

    Level 100 / Level 20 のような数値順には
    あえて変更しない。

    各言語版で命名規則が違うため、
    現時点では「実際のタイトル順」を優先する。
    """

    return sorted(
        pages,
        key=lambda row: (
            row["title"].casefold(),
            row["url"]
        )
    )


# =============================================================================
# TXT生成
# =============================================================================

def save_language_txt(
    language,
    categories
):

    file_path = os.path.join(
        OUTPUT_DIR,
        f"{language}.txt"
    )

    # -------------------------------------------------------------------------
    # 総ページ数
    # -------------------------------------------------------------------------

    total = sum(
        len(pages)
        for pages in categories.values()
    )

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 80
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - "
            f"{language} Level List\n"
        )

        f.write(
            f"総ページ数: {total}\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )

        # ---------------------------------------------------------------------
        # カテゴリ順
        # ---------------------------------------------------------------------

        sorted_categories = sorted(
            categories.keys(),
            key=category_sort_key
        )

        for category in sorted_categories:

            pages = sort_pages(
                categories[
                    category
                ]
            )

            if not pages:
                continue

            label = CATEGORY_LABELS.get(
                category,
                category
            )

            f.write(
                f"[{label}] "
                f"({category})\n"
            )

            f.write(
                "-" * 80
                + "\n"
            )

            for i, page in enumerate(
                pages,
                start=1
            ):

                title = page[
                    "title"
                ]

                url = page[
                    "url"
                ]

                source_type = page[
                    "source_type"
                ]

                # -------------------------------------------------------------
                # メイン行
                # -------------------------------------------------------------

                f.write(
                    f"[{i:04d}] "
                    f"{title}\n"
                )

                # -------------------------------------------------------------
                # URL
                # -------------------------------------------------------------

                f.write(
                    f"      URL: "
                    f"{url}\n"
                )

                # -------------------------------------------------------------
                # ソース
                # -------------------------------------------------------------

                if source_type:

                    f.write(
                        f"      SOURCE: "
                        f"{source_type}\n"
                    )

                # -------------------------------------------------------------
                # 元一覧
                # -------------------------------------------------------------

                if page["indexes"]:

                    f.write(
                        f"      INDEX: "
                        f"{page['indexes']}\n"
                    )

                # -------------------------------------------------------------
                # 翻訳セクション
                # -------------------------------------------------------------

                if page["translation_section"]:

                    f.write(
                        f"      "
                        f"TRANSLATION_SECTION: "
                        f"{page['translation_section']}\n"
                    )

                f.write(
                    "\n"
                )

            f.write(
                "\n"
            )

    return file_path, total


# =============================================================================
# 全体一覧TXT
# =============================================================================

def save_summary_txt(
    database
):

    file_path = os.path.join(
        OUTPUT_DIR,
        "_summary.txt"
    )

    with open(
        file_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 80
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - Level List Summary\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )

        languages = sorted(
            database.keys()
        )

        for language in languages:

            categories = database[
                language
            ]

            total = sum(
                len(pages)
                for pages in categories.values()
            )

            f.write(
                f"{language}: "
                f"{total} 件\n"
            )

            for category in sorted(
                categories.keys(),
                key=category_sort_key
            ):

                label = CATEGORY_LABELS.get(
                    category,
                    category
                )

                count = len(
                    categories[
                        category
                    ]
                )

                f.write(
                    f"    {label}: "
                    f"{count} 件\n"
                )

            f.write(
                "\n"
            )

    return file_path


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print(
        "=" * 80
    )

    print(
        " Backrooms Fandom JP - Level List Generator"
    )

    print(
        "=" * 80
    )

    # -------------------------------------------------------------------------
    # 入力確認
    # -------------------------------------------------------------------------

    print()
    print(
        f"入力: {INPUT_CSV}"
    )

    print(
        f"出力: {OUTPUT_DIR}"
    )

    # -------------------------------------------------------------------------
    # CSV読み込み
    # -------------------------------------------------------------------------

    rows = load_master_csv()

    if not rows:

        print()
        print(
            "処理対象のページがありません。"
        )

        return

    print()
    print(
        f"Master DB読み込み: "
        f"{len(rows)} 件"
    )

    # -------------------------------------------------------------------------
    # DB構築
    # -------------------------------------------------------------------------

    database = build_language_database(
        rows
    )

    # -------------------------------------------------------------------------
    # 出力フォルダ
    # -------------------------------------------------------------------------

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    # -------------------------------------------------------------------------
    # 言語一覧
    # -------------------------------------------------------------------------

    generated_files = []

    print()
    print(
        "=" * 70
    )

    print(
        "言語別Level List生成"
    )

    print(
        "=" * 70
    )

    for language in sorted(
        database.keys()
    ):

        file_path, total = save_language_txt(
            language,
            database[
                language
            ]
        )

        generated_files.append(
            file_path
        )

        print(
            f"{language:<10} "
            f"{total:>5} 件"
        )

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------

    summary_path = save_summary_txt(
        database
    )

    # -------------------------------------------------------------------------
    # 完了
    # -------------------------------------------------------------------------

    print()
    print(
        "=" * 80
    )

    print(
        "Level List生成完了"
    )

    print(
        "=" * 80
    )

    print()
    print(
        f"生成ファイル数: "
        f"{len(generated_files)}"
    )

    for file_path in generated_files:

        print(
            f"  {file_path}"
        )

    print(
        f"  {summary_path}"
    )


# =============================================================================
# 実行
# =============================================================================

if __name__ == "__main__":

    main()