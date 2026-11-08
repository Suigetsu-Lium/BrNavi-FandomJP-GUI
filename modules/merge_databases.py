"""
Backrooms Navigation System - Fandom JP
Master Database Merger

以下の2つのデータベースを統合する。

    parsed_index/pages_database_classified.csv
    parsed_index/translation_database.csv

出力:

    parsed_index/pages_master.csv

URLを一意キーとして統合し、
Fandom JP内に存在するページを1つのマスターデータベースにまとめる。

主な項目:

    title
    url
    category
    language
    source_type
    indexes
    translation_section
    section_number
    classification_reason

注意:
    元のCSVは変更しない。
"""


import csv
import os


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


CLASSIFIED_CSV = os.path.join(
    PARSED_DIR,
    "pages_database_classified.csv"
)

TRANSLATION_CSV = os.path.join(
    PARSED_DIR,
    "translation_database.csv"
)

MASTER_CSV = os.path.join(
    PARSED_DIR,
    "pages_master.csv"
)

MASTER_TXT = os.path.join(
    PARSED_DIR,
    "pages_master.txt"
)

DUPLICATES_TXT = os.path.join(
    PARSED_DIR,
    "master_duplicates.txt"
)


# =============================================================================
# マスターCSVの列
# =============================================================================

FIELDNAMES = [
    "title",
    "url",
    "category",
    "language",
    "source_type",
    "indexes",
    "translation_section",
    "section_number",
    "classification_reason",
]


# =============================================================================
# CSV読み込み
# =============================================================================

def load_csv(file_path):
    """
    CSVを読み込む。
    """

    if not os.path.exists(
        file_path
    ):
        print(
            f"【エラー】CSVが見つかりません:\n"
            f"  {file_path}"
        )

        return []

    with open(
        file_path,
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
# 空文字を正規化
# =============================================================================

def clean(value):
    """
    None / 空白を空文字にする。
    """

    if value is None:
        return ""

    return str(value).strip()


# =============================================================================
# レコード作成
# =============================================================================

def make_record(
    title="",
    url="",
    category="unknown",
    language="unknown",
    source_type="",
    indexes="",
    translation_section="",
    section_number="",
    classification_reason="",
):
    """
    マスターDB用の統一レコードを作る。
    """

    return {
        "title": clean(title),
        "url": clean(url),
        "category": clean(category) or "unknown",
        "language": clean(language) or "unknown",
        "source_type": clean(source_type),
        "indexes": clean(indexes),
        "translation_section": clean(
            translation_section
        ),
        "section_number": clean(
            section_number
        ),
        "classification_reason": clean(
            classification_reason
        ),
    }


# =============================================================================
# pipe区切り値の統合
# =============================================================================

def merge_pipe_values(
    old_value,
    new_value
):
    """
    A|B|C のような値を重複なしで統合する。
    """

    values = []

    for value in (
        old_value,
        new_value,
    ):

        value = clean(value)

        if not value:
            continue

        for part in value.split("|"):

            part = part.strip()

            if not part:
                continue

            if part not in values:
                values.append(part)

    return "|".join(values)


# =============================================================================
# 2レコード統合
# =============================================================================

def merge_two_records(
    base,
    incoming
):
    """
    同一URLの2つのレコードを統合する。
    """

    result = base.copy()

    # -------------------------------------------------------------------------
    # title
    # -------------------------------------------------------------------------

    if (
        not result["title"]
        and incoming["title"]
    ):
        result["title"] = incoming["title"]

    # -------------------------------------------------------------------------
    # category
    # -------------------------------------------------------------------------

    if (
        result["category"] == "unknown"
        and incoming["category"]
        and incoming["category"] != "unknown"
    ):
        result["category"] = incoming["category"]

    # -------------------------------------------------------------------------
    # language
    # -------------------------------------------------------------------------

    if (
        result["language"] == "unknown"
        and incoming["language"]
        and incoming["language"] != "unknown"
    ):
        result["language"] = incoming["language"]

    # -------------------------------------------------------------------------
    # source_type
    # -------------------------------------------------------------------------

    source_values = []

    for value in (
        result["source_type"],
        incoming["source_type"],
    ):

        value = clean(value)

        if value and value not in source_values:

            source_values.append(value)

    result["source_type"] = "|".join(
        source_values
    )

    # -------------------------------------------------------------------------
    # indexes
    # -------------------------------------------------------------------------

    result["indexes"] = merge_pipe_values(
        result["indexes"],
        incoming["indexes"]
    )

    # -------------------------------------------------------------------------
    # translation_section
    # -------------------------------------------------------------------------

    result["translation_section"] = merge_pipe_values(
        result["translation_section"],
        incoming["translation_section"]
    )

    # -------------------------------------------------------------------------
    # section_number
    # -------------------------------------------------------------------------

    result["section_number"] = merge_pipe_values(
        result["section_number"],
        incoming["section_number"]
    )

    # -------------------------------------------------------------------------
    # classification_reason
    # -------------------------------------------------------------------------

    result["classification_reason"] = merge_pipe_values(
        result["classification_reason"],
        incoming["classification_reason"]
    )

    return result


# =============================================================================
# classified DB → master DB
# =============================================================================

def add_classified_records(
    master,
    rows
):
    """
    pages_database_classified.csv をマスターDBへ追加する。
    """

    added = 0
    merged = 0

    for row in rows:

        url = clean(
            row.get("url")
        )

        if not url:
            continue

        record = make_record(
            title=row.get("title"),
            url=url,
            category=row.get("category"),
            language=row.get("language"),
            source_type=row.get("source_type"),
            indexes=row.get("indexes"),
            classification_reason=row.get(
                "classification_reason"
            ),
        )

        if url in master:

            master[url] = merge_two_records(
                master[url],
                record
            )

            merged += 1

        else:

            master[url] = record
            added += 1

    return added, merged


# =============================================================================
# translation DB → master DB
# =============================================================================

def add_translation_records(
    master,
    rows
):
    """
    translation_database.csv をマスターDBへ追加する。
    """

    added = 0
    merged = 0

    for row in rows:

        url = clean(
            row.get("url")
        )

        if not url:
            continue

        record = make_record(
            title=row.get("title"),
            url=url,
            category=row.get("category"),
            language=row.get("language"),
            source_type="translation",
            translation_section=row.get(
                "translation_section"
            ),
            section_number=row.get(
                "section_number"
            ),
        )

        if url in master:

            master[url] = merge_two_records(
                master[url],
                record
            )

            merged += 1

        else:

            master[url] = record
            added += 1

    return added, merged


# =============================================================================
# CSV保存
# =============================================================================

def save_master_csv(
    records
):
    """
    マスターCSVを保存する。
    """

    sorted_records = sorted(
        records.values(),
        key=lambda row: (
            row["language"],
            row["category"],
            row["title"].lower(),
            row["url"],
        )
    )

    with open(
        MASTER_CSV,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=FIELDNAMES
        )

        writer.writeheader()

        writer.writerows(
            sorted_records
        )

    return sorted_records


# =============================================================================
# TXT保存
# =============================================================================

def save_master_txt(
    records
):
    """
    人間が確認しやすいTXTを保存する。
    """

    with open(
        MASTER_TXT,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 100
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - Master Database\n"
        )

        f.write(
            f"総件数: {len(records)}\n"
        )

        f.write(
            "=" * 100
            + "\n\n"
        )

        for i, row in enumerate(
            records,
            start=1
        ):

            f.write(
                f"[{i:04d}] "
                f"{row['title']}\n"
            )

            f.write(
                f"  URL: "
                f"{row['url']}\n"
            )

            f.write(
                f"  category: "
                f"{row['category']}\n"
            )

            f.write(
                f"  language: "
                f"{row['language']}\n"
            )

            f.write(
                f"  source_type: "
                f"{row['source_type']}\n"
            )

            f.write(
                f"  indexes: "
                f"{row['indexes']}\n"
            )

            if row["translation_section"]:

                f.write(
                    f"  translation_section: "
                    f"{row['translation_section']}\n"
                )

            if row["section_number"]:

                f.write(
                    f"  section_number: "
                    f"{row['section_number']}\n"
                )

            if row["classification_reason"]:

                f.write(
                    f"  classification_reason: "
                    f"{row['classification_reason']}\n"
                )

            f.write("\n")


# =============================================================================
# 重複情報保存
# =============================================================================

def save_duplicate_report(
    master,
    classified_rows,
    translation_rows
):
    """
    両方のDBに存在していたURLを記録する。
    """

    classified_urls = {
        clean(row.get("url"))
        for row in classified_rows
        if clean(row.get("url"))
    }

    translation_urls = {
        clean(row.get("url"))
        for row in translation_rows
        if clean(row.get("url"))
    }

    duplicates = sorted(
        classified_urls & translation_urls
    )

    with open(
        DUPLICATES_TXT,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 80
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - Master DB Duplicate URLs\n"
        )

        f.write(
            f"重複URL数: {len(duplicates)}\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )

        for i, url in enumerate(
            duplicates,
            start=1
        ):

            title = master.get(
                url,
                {}
            ).get(
                "title",
                ""
            )

            f.write(
                f"[{i:04d}] {title}\n"
            )

            f.write(
                f"      {url}\n\n"
            )

    return duplicates


# =============================================================================
# 統計
# =============================================================================

def print_statistics(
    records
):
    """
    マスターDBの統計を表示する。
    """

    category_counts = {}
    language_counts = {}
    source_counts = {}

    for row in records:

        category = row["category"]
        language = row["language"]
        source_type = row["source_type"]

        category_counts[category] = (
            category_counts.get(
                category,
                0
            ) + 1
        )

        language_counts[language] = (
            language_counts.get(
                language,
                0
            ) + 1
        )

        source_counts[source_type] = (
            source_counts.get(
                source_type,
                0
            ) + 1
        )

    print()
    print("=" * 80)
    print(
        "Master Database 統計"
    )
    print("=" * 80)

    print()
    print("[カテゴリ]")

    for key in sorted(
        category_counts
    ):

        print(
            f"  {key:<20}"
            f"{category_counts[key]:>6} 件"
        )

    print()
    print("[言語]")

    for key in sorted(
        language_counts
    ):

        print(
            f"  {key:<20}"
            f"{language_counts[key]:>6} 件"
        )

    print()
    print("[ソース]")

    for key in sorted(
        source_counts
    ):

        print(
            f"  {key:<20}"
            f"{source_counts[key]:>6} 件"
        )


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print("=" * 80)
    print(
        " Backrooms Fandom JP - Master Database Merger"
    )
    print("=" * 80)

    print()
    print(
        "入力1:"
    )

    print(
        f"  {CLASSIFIED_CSV}"
    )

    print(
        "入力2:"
    )

    print(
        f"  {TRANSLATION_CSV}"
    )

    print(
        "出力:"
    )

    print(
        f"  {MASTER_CSV}"
    )

    # -------------------------------------------------------------------------
    # CSV読み込み
    # -------------------------------------------------------------------------

    classified_rows = load_csv(
        CLASSIFIED_CSV
    )

    translation_rows = load_csv(
        TRANSLATION_CSV
    )

    print()
    print(
        f"pages_database_classified: "
        f"{len(classified_rows)} 件"
    )

    print(
        f"translation_database: "
        f"{len(translation_rows)} 件"
    )

    # -------------------------------------------------------------------------
    # Master作成
    # -------------------------------------------------------------------------

    master = {}

    added_classified, merged_classified = (
        add_classified_records(
            master,
            classified_rows
        )
    )

    added_translation, merged_translation = (
        add_translation_records(
            master,
            translation_rows
        )
    )

    # -------------------------------------------------------------------------
    # CSV
    # -------------------------------------------------------------------------

    sorted_records = save_master_csv(
        master
    )

    # -------------------------------------------------------------------------
    # TXT
    # -------------------------------------------------------------------------

    save_master_txt(
        sorted_records
    )

    # -------------------------------------------------------------------------
    # 重複レポート
    # -------------------------------------------------------------------------

    duplicates = save_duplicate_report(
        master,
        classified_rows,
        translation_rows
    )

    # -------------------------------------------------------------------------
    # 統計
    # -------------------------------------------------------------------------

    print()
    print(
        "統合結果:"
    )

    print(
        f"  classified 新規追加: "
        f"{added_classified} 件"
    )

    print(
        f"  classified 重複統合: "
        f"{merged_classified} 件"
    )

    print(
        f"  translation 新規追加: "
        f"{added_translation} 件"
    )

    print(
        f"  translation 重複統合: "
        f"{merged_translation} 件"
    )

    print(
        f"  両DBに存在したURL: "
        f"{len(duplicates)} 件"
    )

    print(
        f"  Master総件数: "
        f"{len(sorted_records)} 件"
    )

    print_statistics(
        sorted_records
    )

    # -------------------------------------------------------------------------
    # 完了
    # -------------------------------------------------------------------------

    print()
    print("=" * 80)
    print(
        "Master Database 作成完了"
    )
    print("=" * 80)

    print()
    print(
        "生成ファイル:"
    )

    print(
        f"  {MASTER_CSV}"
    )

    print(
        f"  {MASTER_TXT}"
    )

    print(
        f"  {DUPLICATES_TXT}"
    )


# =============================================================================
# 実行
# =============================================================================

if __name__ == "__main__":
    main()