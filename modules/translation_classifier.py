"""
Backrooms Navigation System - Fandom JP
Translation Classifier v3

translation_structure.txt を解析し、
翻訳記事のカテゴリ・言語を分類する。

v3 改良点:
    ・見出し末尾の [ ] / [x] 等を除去
    ・「Level 0 [ ]」等を正しく亜階層として判定
    ・「番外階層 [ ]」等を正しく認識
    ・通常階層の番号帯をより安全に判定
"""

import csv
import os
import re


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

INPUT_FILE = os.path.join(
    PARSED_DIR,
    "translation_structure.txt"
)

OUTPUT_CSV = os.path.join(
    PARSED_DIR,
    "translation_database.csv"
)

UNKNOWN_FILE = os.path.join(
    PARSED_DIR,
    "translation_unknown.txt"
)


# =============================================================================
# 見出し正規化
# =============================================================================

def normalize_heading(name):
    """
    一覧HTML由来の見出しを正規化する。

    例:
        "Level 0 [ ]"        -> "Level 0"
        "Level 6 [ ]"        -> "Level 6"
        "番外階層 [ ]"       -> "番外階層"
        "次元 [ ]"           -> "次元"
        "国外階層 [ ]"       -> "国外階層"
        "0-8(メインナイン) [ ]" -> "0-8(メインナイン)"
    """

    name = name.strip()

    # 末尾のチェックボックス表記を削除
    name = re.sub(
        r"\s*\[[ xX✓✔]?\]\s*$",
        "",
        name
    )

    # 末尾の空白を再除去
    name = name.strip()

    return name


# =============================================================================
# 言語コード
# =============================================================================

LANGUAGE_PATTERNS = {

    "FR": [
        re.compile(r"\bFR[- ]\d", re.IGNORECASE),
        re.compile(r"\bFR\b", re.IGNORECASE),
    ],

    "UA": [
        re.compile(r"\bUA[- ]\d", re.IGNORECASE),
        re.compile(r"\bUA\b", re.IGNORECASE),
    ],

    "PL": [
        re.compile(r"\bPL[- ]\d", re.IGNORECASE),
        re.compile(r"\bPL\b", re.IGNORECASE),
    ],

    "ZH": [
        re.compile(r"\bZH[- ]\d", re.IGNORECASE),
        re.compile(r"\bZH\b", re.IGNORECASE),
    ],

    "IT": [
        re.compile(r"\bIT[- ]\d", re.IGNORECASE),
        re.compile(r"\bIT\b", re.IGNORECASE),
    ],

    "PT": [
        re.compile(r"\bPT[- ]\d", re.IGNORECASE),
        re.compile(r"\bPT\b", re.IGNORECASE),
    ],
}


# =============================================================================
# カテゴリ判定
# =============================================================================

def detect_category(section_name):
    """
    翻訳一覧の見出しからカテゴリを判定する。
    """

    name = normalize_heading(
        section_name
    )

    # -------------------------------------------------------------------------
    # 通常階層
    # -------------------------------------------------------------------------

    # 0-8(メインナイン)
    # 9-99
    # 100-199
    # 200-299
    # ...
    # 1000-
    #
    # 「数字-数字」または「数字-」形式
    if re.fullmatch(
        r"-?\d+\s*-\s*\d+.*",
        name
    ):
        return "normal"

    if re.fullmatch(
        r"-?\d+\s*-\s*.*",
        name
    ):
        return "normal"

    # 特殊な通常階層範囲
    if name.startswith("0~-"):
        return "normal"

    if name == "-7~":
        return "normal"

    # -------------------------------------------------------------------------
    # 亜階層
    # -------------------------------------------------------------------------

    # 例:
    # Level 0
    # Level 1
    # Level 6
    # Level 9
    # Level 37
    #
    # 小数は現時点では見出し側には存在しないため、
    # 整数中心にする。
    if re.fullmatch(
        r"Level\s+-?\d+",
        name,
        re.IGNORECASE
    ):
        return "sublevel"

    # -------------------------------------------------------------------------
    # 番外階層
    # -------------------------------------------------------------------------

    if name == "番外階層":
        return "anomalous"

    # -------------------------------------------------------------------------
    # 次元
    # -------------------------------------------------------------------------

    if name == "次元":
        return "dimension"

    # -------------------------------------------------------------------------
    # 国外階層
    # -------------------------------------------------------------------------

    if name == "国外階層":
        return "foreign"

    # -------------------------------------------------------------------------
    # エンティティ
    # -------------------------------------------------------------------------

    if name == "エンティティ":
        return "entity"

    # -------------------------------------------------------------------------
    # 物品
    # -------------------------------------------------------------------------

    if name == "物品":
        return "item"

    # -------------------------------------------------------------------------
    # 現象
    # -------------------------------------------------------------------------

    if name == "現象":
        return "phenomenon"

    # -------------------------------------------------------------------------
    # カノン
    # -------------------------------------------------------------------------

    if name == "カノン":
        return "canon"

    # -------------------------------------------------------------------------
    # ジョーク階層
    # -------------------------------------------------------------------------

    if name == "ジョーク階層":
        return "joke_level"

    # -------------------------------------------------------------------------
    # ジョーク記事
    # -------------------------------------------------------------------------

    if name == "ジョーク記事":
        return "joke_article"

    # -------------------------------------------------------------------------
    # 国外ジョーク記事
    # -------------------------------------------------------------------------

    if name == "国外ジョーク記事":
        return "foreign_joke"

    # -------------------------------------------------------------------------
    # 人物
    # -------------------------------------------------------------------------

    if name == "人物":
        return "person"

    return "unknown"


# =============================================================================
# 言語判定
# =============================================================================

def detect_language(
    title,
    section_name,
    category
):
    """
    翻訳元言語を推定する。

    基本:
        通常の翻訳 → EN

    明示的な国別コードがあれば優先。
    """

    text = (
        f"{section_name} {title}"
    )

    # 明示的な言語コード
    for language, patterns in LANGUAGE_PATTERNS.items():

        for pattern in patterns:

            if pattern.search(text):
                return language

    # 基本の翻訳元言語
    return "EN"


# =============================================================================
# SECTION番号
# =============================================================================

def parse_section_number(line):

    match = re.match(
        r"\[SECTION\s+(\d+)\]",
        line
    )

    if not match:
        return ""

    return match.group(1)


# =============================================================================
# 構造ファイル解析
# =============================================================================

def parse_structure():

    if not os.path.exists(
        INPUT_FILE
    ):
        raise FileNotFoundError(
            f"ファイルがありません:\n{INPUT_FILE}"
        )

    records = []

    current_section = ""
    current_section_number = ""
    current_category = "unknown"

    with open(
        INPUT_FILE,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        lines = [
            line.rstrip("\n")
            for line in f
        ]

    i = 0

    while i < len(lines):

        line = lines[i].strip()

        # ---------------------------------------------------------------------
        # SECTION
        # ---------------------------------------------------------------------

        if line.startswith(
            "[SECTION "
        ):

            current_section_number = parse_section_number(
                line
            )

            current_section = ""
            current_category = "unknown"

            i += 1
            continue

        # ---------------------------------------------------------------------
        # 見出し
        # ---------------------------------------------------------------------

        if line.startswith(
            "見出し:"
        ):

            raw_heading = line.split(
                ":",
                1
            )[1].strip()

            current_section = normalize_heading(
                raw_heading
            )

            current_category = detect_category(
                current_section
            )

            i += 1
            continue

        # ---------------------------------------------------------------------
        # 記事タイトル
        # ---------------------------------------------------------------------

        match = re.match(
            r"\[\d+\]\s+(.*)$",
            line
        )

        if match:

            title = match.group(1).strip()

            # 次行URL
            if i + 1 < len(lines):

                url = lines[i + 1].strip()

                if url.startswith(
                    "https://"
                ):

                    language = detect_language(
                        title,
                        current_section,
                        current_category
                    )

                    records.append({
                        "title": title,
                        "url": url,
                        "translation_section": current_section,
                        "section_number": current_section_number,
                        "category": current_category,
                        "language": language,
                        "source_type": "translation",
                    })

                    i += 2
                    continue

        i += 1

    return records


# =============================================================================
# URL重複統合
# =============================================================================

def merge_records(records):

    merged = {}

    for record in records:

        url = record["url"]

        if url not in merged:

            merged[url] = record.copy()
            continue

        existing = merged[url]

        # ---------------------------------------------------------------------
        # セクション統合
        # ---------------------------------------------------------------------

        sections = []

        for section in (
            existing["translation_section"],
            record["translation_section"],
        ):

            if section not in sections:
                sections.append(section)

        existing["translation_section"] = (
            " | ".join(sections)
        )

        # ---------------------------------------------------------------------
        # SECTION番号統合
        # ---------------------------------------------------------------------

        numbers = []

        for number in (
            existing["section_number"],
            record["section_number"],
        ):

            if number and number not in numbers:
                numbers.append(number)

        existing["section_number"] = (
            " | ".join(numbers)
        )

        # ---------------------------------------------------------------------
        # カテゴリ
        # ---------------------------------------------------------------------

        if existing["category"] == "unknown":

            existing["category"] = (
                record["category"]
            )

        # ---------------------------------------------------------------------
        # 言語
        # ---------------------------------------------------------------------

        if (
            existing["language"] == "EN"
            and record["language"] != "EN"
        ):

            existing["language"] = (
                record["language"]
            )

    result = list(
        merged.values()
    )

    result.sort(
        key=lambda x: (
            x["category"],
            x["language"],
            x["title"],
            x["url"]
        )
    )

    return result


# =============================================================================
# CSV保存
# =============================================================================

def save_csv(records):

    fieldnames = [
        "title",
        "url",
        "translation_section",
        "section_number",
        "category",
        "language",
        "source_type",
    ]

    with open(
        OUTPUT_CSV,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(records)


# =============================================================================
# 不明分類保存
# =============================================================================

def save_unknown(records):

    unknown_records = [
        record
        for record in records
        if record["category"] == "unknown"
    ]

    with open(
        UNKNOWN_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 80
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - Translation Classification Unknown\n"
        )

        f.write(
            f"件数: {len(unknown_records)}\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )

        for i, record in enumerate(
            unknown_records,
            start=1
        ):

            f.write(
                f"[{i:04d}] {record['title']}\n"
            )

            f.write(
                f"      URL: {record['url']}\n"
            )

            f.write(
                f"      セクション: "
                f"{record['translation_section']}\n"
            )

            f.write(
                f"      category: "
                f"{record['category']}\n"
            )

            f.write(
                f"      language: "
                f"{record['language']}\n\n"
            )

    return len(
        unknown_records
    )


# =============================================================================
# 統計
# =============================================================================

def print_statistics(records):

    category_counts = {}
    language_counts = {}

    for record in records:

        category = record["category"]
        language = record["language"]

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

    print()
    print("=" * 80)
    print(
        "翻訳記事分類統計 v3"
    )
    print("=" * 80)

    print()
    print("[カテゴリ]")

    for key in sorted(
        category_counts
    ):

        print(
            f"  {key:<16}"
            f"{category_counts[key]:>5} 件"
        )

    print()
    print("[言語]")

    for key in sorted(
        language_counts
    ):

        print(
            f"  {key:<16}"
            f"{language_counts[key]:>5} 件"
        )


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print("=" * 80)
    print(
        " Backrooms Fandom JP - Translation Classifier v3"
    )
    print("=" * 80)

    records = parse_structure()

    print()
    print(
        f"構造解析レコード数: {len(records)}"
    )

    merged_records = merge_records(
        records
    )

    print(
        f"URL重複統合後: {len(merged_records)}"
    )

    save_csv(
        merged_records
    )

    unknown_count = save_unknown(
        merged_records
    )

    print()
    print(
        f"CSV保存先:"
    )

    print(
        f"  {OUTPUT_CSV}"
    )

    print(
        f"未知カテゴリ: {unknown_count} 件"
    )

    print_statistics(
        merged_records
    )

    print()
    print("=" * 80)
    print(
        "翻訳記事分類完了"
    )
    print("=" * 80)


if __name__ == "__main__":
    main()