"""
Backrooms Navigation System - Fandom JP
Page Classifier

pages_database.csv に対して、

    category
    language
    source_type
    classification_reason

を追加する。

重要:
    この段階では、無理に分類を確定しない。
    判定できないものは unknown として残す。

入力:
    parsed_index/pages_database.csv

出力:
    parsed_index/pages_database_classified.csv
"""

import csv
import json
import os
import re

from modules.runtime_paths import get_application_dir

# =============================================================================
# 基本パス
# =============================================================================

BASE_DIR = str(get_application_dir())

PARSED_DIR = os.path.join(
    BASE_DIR,
    "parsed_index"
)

INPUT_CSV = os.path.join(
    PARSED_DIR,
    "pages_database.csv"
)

OUTPUT_CSV = os.path.join(
    PARSED_DIR,
    "pages_database_classified.csv"
)


# =============================================================================
# 言語版判定
# =============================================================================

# 「Level XX-数字」「Level XX 数字」のように、
# タイトル上で明確に言語版を示しているものだけを登録する。
#
# A は現時点では除外。
# "Level A-95" のように見えても、
# 本当に言語版Aなのか、別の命名なのかをタイトルだけでは
# 確実に判断できないため。
LANGUAGE_PATTERNS = {
    "PL": [
        re.compile(r"^Level\s+PL-\S+", re.IGNORECASE),
        re.compile(r"^Level\s+PL\s+\S+", re.IGNORECASE),
    ],

    "UA": [
        re.compile(r"^Level\s+UA-\S+", re.IGNORECASE),
        re.compile(r"^Level\s+UA\s+\S+", re.IGNORECASE),
    ],

    "ZH": [
        re.compile(r"^Level\s+ZH-\S+", re.IGNORECASE),
        re.compile(r"^Level\s+ZH\s+\S+", re.IGNORECASE),
    ],

    "FR": [
        re.compile(r"^Level\s+FR-\S+", re.IGNORECASE),
        re.compile(r"^Level\s+FR\s+\S+", re.IGNORECASE),
    ],

    "IT": [
        re.compile(r"^Level\s+IT-\S+", re.IGNORECASE),
        re.compile(r"^Level\s+IT\s+\S+", re.IGNORECASE),
    ],

    "PT": [
        re.compile(r"^Level\s+PT-\S+", re.IGNORECASE),
        re.compile(r"^Level\s+PT\s+\S+", re.IGNORECASE),
    ],
}


# =============================================================================
# η判定
# =============================================================================

ETA_PATTERN = re.compile(
    r"η\s*$"
)


def has_eta(title):
    """
    ページタイトルがη階層形式か判定する。
    """

    if not title:
        return False

    title = title.strip()

    return bool(
        ETA_PATTERN.search(title)
    )


# =============================================================================
# 言語判定
# =============================================================================

def detect_language(title):
    """
    タイトルから明確に判定できる言語版を返す。

    判定できない場合:
        unknown
    """

    if not title:
        return "unknown"

    title = title.strip()

    for language, patterns in LANGUAGE_PATTERNS.items():

        for pattern in patterns:

            if pattern.search(title):
                return language

    return "unknown"


# =============================================================================
# source_type 判定
# =============================================================================

def detect_source_type(
    title,
    indexes
):
    """
    翻訳記事かFandom JP独自記事かを判定する。

    translation 一覧に所属しているものは translation。

    それ以外は original。
    """

    index_set = set(
        x.strip()
        for x in indexes.split("|")
        if x.strip()
    )

    if "translation" in index_set:

        return "translation"

    return "original"


# =============================================================================
# category 判定
# =============================================================================

def load_index_memberships(value):
    """Master前段CSVの一覧所属JSONを安全に読み込む。"""
    try:
        memberships = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in memberships if isinstance(item, dict)]


def _membership_category(index_name, section_path, title):
    """一覧ページIDと見出し階層から、その掲載位置のカテゴリを判定する。"""
    headings = [
        str(item.get("heading") or "").strip()
        for item in section_path
        if isinstance(item, dict) and item.get("heading")
    ]
    normalized = [re.sub(r"\s*\[[ xX✓✔]?\]\s*$", "", value).strip() for value in headings]
    joined = " > ".join(normalized)

    if index_name == "normal_eta":
        return "normal_eta"
    if index_name == "normal":
        return "normal"
    if index_name == "enigmatic":
        if any(re.search(r"番外亜階層\s*η", value) for value in normalized):
            return "enigmatic_sublevel_eta"
        if any(re.search(r"番外階層\s*η", value) for value in normalized):
            return "enigmatic_eta"
        if any("無印番外階層" in value or value == "番外階層" for value in normalized):
            return "enigmatic"
        return "enigmatic"
    if index_name == "sublevel":
        if any(re.search(r"番外亜階層\s*η", value) for value in normalized):
            return "enigmatic_sublevel_eta"
        if any(re.search(r"通常亜階層\s*η", value) for value in normalized):
            return "sublevel_eta"
        if any("無印通常亜階層" in value or value == "亜階層" for value in normalized):
            return "sublevel"
        return "sublevel"
    if index_name == "hierarchy":
        if any(re.search(r"番外階層\s*η", value) for value in normalized):
            return "enigmatic_eta"
        if any(re.search(r"通常階層\s*η", value) for value in normalized):
            return "normal_eta"
        if any("番外階層" in value for value in normalized):
            return "enigmatic"
        if any("亜階層" in value for value in normalized):
            return "sublevel_eta" if any("η" in value for value in normalized) else "sublevel"
        if any("通常階層" in value for value in normalized):
            return "normal"
        # 階層一覧に分類見出しがない旧HTMLに限り、既存の一般的なη形式を補助利用する。
        return "normal_eta" if has_eta(title) else "normal"
    if index_name == "alias":
        return "alias"
    if index_name == "terms":
        return "terms"
    if index_name == "contest":
        return "contest"
    return None


INDEX_SOURCE_PRIORITY = {
    "normal_eta": 0,
    "enigmatic": 1,
    "sublevel": 2,
    "normal": 3,
    "hierarchy": 4,
    "alias": 5,
    "terms": 6,
    "contest": 7,
}

CATEGORY_SOURCE_PRIORITY = {
    "normal_eta": 0,
    "enigmatic_eta": 1,
    "enigmatic_sublevel_eta": 2,
    "sublevel_eta": 3,
    "enigmatic": 4,
    "sublevel": 5,
    "normal": 6,
    "alias": 7,
    "terms": 8,
    "contest": 9,
}


def detect_category(title, indexes, index_memberships=""):
    """Wiki一覧の掲載所属・見出し階層を一次根拠としてカテゴリを決める。"""
    memberships = load_index_memberships(index_memberships)
    candidates = []
    for membership in memberships:
        category = _membership_category(
            str(membership.get("index_name") or ""),
            membership.get("section_path") or [],
            title,
        )
        if category:
            index_name = str(membership.get("index_name") or "")
            candidates.append((
                INDEX_SOURCE_PRIORITY.get(index_name, 99),
                CATEGORY_SOURCE_PRIORITY.get(category, 99),
                int(membership.get("section_order") or 0),
                int(membership.get("page_order") or 0),
                category,
                index_name,
            ))

    if candidates:
        candidates.sort()
        selected = candidates[0]
        return selected[4], (
            f"index={selected[5]};"
            f"section_order={selected[2]};page_order={selected[3]}"
        )

    index_set = {value.strip() for value in indexes.split("|") if value.strip()}
    if "normal_eta" in index_set:
        return "normal_eta", "legacy_index=normal_eta"
    if "enigmatic" in index_set:
        category = "enigmatic_eta" if has_eta(title) else "enigmatic"
        return category, "legacy_index=enigmatic_without_section"
    if "sublevel" in index_set:
        category = "sublevel_eta" if has_eta(title) else "sublevel"
        return category, "legacy_index=sublevel_without_section"
    if "normal" in index_set or "hierarchy" in index_set:
        return ("normal_eta" if has_eta(title) else "normal"), "legacy_hierarchy_fallback"
    if "alias" in index_set:
        return "alias", "legacy_index=alias"
    if "terms" in index_set:
        return "terms", "legacy_index=terms"
    if "contest" in index_set:
        return "contest", "legacy_index=contest"
    if "translation" in index_set:
        return "unknown", "index=translation_only"
    return "unknown", "no_matching_index"


# =============================================================================
# 言語とsourceから最終的な判定
# =============================================================================

def classify_row(row):
    """
    CSVの1行を分類する。
    """

    title = (
        row.get("title")
        or ""
    ).strip()

    indexes = (
        row.get("indexes")
        or ""
    ).strip()

    index_memberships = row.get("index_memberships") or "[]"
    category, category_reason = detect_category(
        title,
        indexes,
        index_memberships,
    )
    selected_membership = {}
    selected_index = category_reason.split(";", 1)[0]
    if selected_index.startswith("index="):
        selected_index = selected_index[len("index="):]
    for membership in load_index_memberships(index_memberships):
        if membership.get("index_name") == selected_index:
            selected_membership = membership
            break
    section_path = json.dumps(
        selected_membership.get("section_path", []), ensure_ascii=False
    ) if selected_membership else row.get("section_path", "[]")
    section_order = selected_membership.get("section_order", row.get("section_order", "0"))
    page_order = selected_membership.get("page_order", row.get("page_order", "0"))

    source_type = detect_source_type(
        title,
        indexes
    )

    language = detect_language(
        title
    )

    # -------------------------------------------------------------------------
    # 翻訳記事だが言語が判定できない場合
    # -------------------------------------------------------------------------

    if (
        source_type == "translation"
        and language == "unknown"
    ):

        language_reason = (
            "translation_but_language_unknown"
        )

    elif language != "unknown":

        language_reason = (
            f"title_language={language}"
        )

    else:

        # Fandom JPオリジナル記事で、
        # 明示的な言語版表記がないものはJP扱い。
        #
        # ただし「本当に日本語オリジナルなのか」を
        # 完全保証するものではない。
        if source_type == "original":

            language = "JP"

            language_reason = (
                "original_default=JP"
            )

        else:

            language_reason = (
                "language_unknown"
            )

    # -------------------------------------------------------------------------
    # 分類理由
    # -------------------------------------------------------------------------

    reason = (
        f"{category_reason};"
        f"{language_reason};"
        f"source={source_type}"
    )

    return {
        "title": title,
        "url": row.get("url", ""),
        "indexes": indexes,
        "index_memberships": index_memberships,
        "section_path": section_path,
        "section_order": section_order,
        "page_order": page_order,
        "category": category,
        "language": language,
        "source_type": source_type,
        "classification_reason": reason,
    }


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
            f"【エラー】入力CSVが見つかりません:\n"
            f"{file_path}"
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

        return list(reader)


# =============================================================================
# CSV保存
# =============================================================================

def save_csv(
    file_path,
    rows
):
    """
    分類済みCSVを保存する。
    """

    fieldnames = [
        "title",
        "url",
        "indexes",
        "index_memberships",
        "section_path",
        "section_order",
        "page_order",
        "category",
        "language",
        "source_type",
        "classification_reason",
    ]

    with open(
        file_path,
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()

        writer.writerows(
            rows
        )


# =============================================================================
# 統計表示
# =============================================================================

def print_statistics(rows):
    """
    分類結果の統計を表示する。
    """

    category_counts = {}
    language_counts = {}
    source_counts = {}

    for row in rows:

        category = row["category"]
        language = row["language"]
        source = row["source_type"]

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

        source_counts[source] = (
            source_counts.get(
                source,
                0
            ) + 1
        )

    print()
    print("=" * 70)
    print("分類統計")
    print("=" * 70)

    print()
    print("[カテゴリ]")

    for key in sorted(
        category_counts
    ):

        print(
            f"  {key:<16} "
            f"{category_counts[key]:>5} 件"
        )

    print()
    print("[言語]")

    for key in sorted(
        language_counts
    ):

        print(
            f"  {key:<16} "
            f"{language_counts[key]:>5} 件"
        )

    print()
    print("[ソース]")

    for key in sorted(
        source_counts
    ):

        print(
            f"  {key:<16} "
            f"{source_counts[key]:>5} 件"
        )


# =============================================================================
# 不明分類一覧保存
# =============================================================================

def save_unknowns(rows):
    """
    category=unknown または language=unknown のページを
    確認用TXTに保存する。

    後から分類規則を改良するために重要。
    """

    unknown_path = os.path.join(
        PARSED_DIR,
        "classification_unknown.txt"
    )

    unknown_rows = []

    for row in rows:

        if (
            row["category"] == "unknown"
            or row["language"] == "unknown"
        ):

            unknown_rows.append(
                row
            )

    with open(
        unknown_path,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "=" * 80
            + "\n"
        )

        f.write(
            "Backrooms Fandom JP - Classification Unknown\n"
        )

        f.write(
            f"対象件数: {len(unknown_rows)}\n"
        )

        f.write(
            "=" * 80
            + "\n\n"
        )

        for i, row in enumerate(
            unknown_rows,
            start=1
        ):

            f.write(
                f"[{i:04d}] "
                f"{row['title']}\n"
            )

            f.write(
                f"      URL: {row['url']}\n"
            )

            f.write(
                f"      一覧: {row['indexes']}\n"
            )

            f.write(
                f"      category: {row['category']}\n"
            )

            f.write(
                f"      language: {row['language']}\n"
            )

            f.write("\n")

    print()
    print(
        f"不明分類一覧: "
        f"{unknown_path}"
    )

    print(
        f"不明件数: "
        f"{len(unknown_rows)}"
    )


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print("=" * 80)
    print(
        " Backrooms Fandom JP - Page Classifier"
    )
    print("=" * 80)

    print()
    print(
        f"入力: {INPUT_CSV}"
    )

    print(
        f"出力: {OUTPUT_CSV}"
    )

    # -------------------------------------------------------------------------
    # 読み込み
    # -------------------------------------------------------------------------

    rows = load_csv(
        INPUT_CSV
    )

    if not rows:

        print()
        print(
            "処理対象がありません。"
        )

        return

    print()
    print(
        f"読み込み件数: "
        f"{len(rows)}"
    )

    # -------------------------------------------------------------------------
    # 分類
    # -------------------------------------------------------------------------

    classified_rows = []

    for row in rows:

        classified = classify_row(
            row
        )

        classified_rows.append(
            classified
        )

    # -------------------------------------------------------------------------
    # 保存
    # -------------------------------------------------------------------------

    save_csv(
        OUTPUT_CSV,
        classified_rows
    )

    # -------------------------------------------------------------------------
    # 統計
    # -------------------------------------------------------------------------

    print_statistics(
        classified_rows
    )

    # -------------------------------------------------------------------------
    # 不明一覧
    # -------------------------------------------------------------------------

    save_unknowns(
        classified_rows
    )

    # -------------------------------------------------------------------------
    # 完了
    # -------------------------------------------------------------------------

    print()
    print("=" * 80)
    print(
        "分類完了"
    )
    print("=" * 80)

    print()
    print(
        f"生成ファイル:"
    )

    print(
        f"  {OUTPUT_CSV}"
    )

    print(
        f"  {os.path.join(PARSED_DIR, 'classification_unknown.txt')}"
    )


# =============================================================================
# 実行
# =============================================================================

if __name__ == "__main__":
    main()