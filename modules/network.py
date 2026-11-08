"""
Backrooms Navigation System - Fandom JP
Network Extraction Module

extracted_fandom_levels_all.txt から
Level間の接続情報を解析し、

    backrooms_edges.csv
    backrooms_nodes.csv

を生成する。
"""

import csv
import os
import re


# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = os.path.dirname(
    os.path.dirname(
        os.path.abspath(__file__)
    )
)

INPUT_FILE = os.path.join(
    BASE_DIR,
    "extracted_fandom_levels_all.txt"
)

EDGES_OUTPUT_FILE = os.path.join(
    BASE_DIR,
    "backrooms_edges.csv"
)

NODES_OUTPUT_FILE = os.path.join(
    BASE_DIR,
    "backrooms_nodes.csv"
)


# =============================================================================
# 正規表現
# =============================================================================

LEVEL_TITLE_PATTERN = re.compile(
    r"^(Level\s+[-0-9A-Za-z.📦🥞🍔🍕🥙仝]+(?:\s*η)?(?:\s*\(\d+\))?|"
    r"Level\s+[A-Za-z\s]+(?:\s*η)?|"
    r"The\s+[-A-Za-z\s]+(?:\s*η)?)"
    r'(?:\s*[:：]|\s+["“])',
    re.IGNORECASE,
)

SECTION_PATTERN = re.compile(
    r"^\[(.*?)\]"
)

WS_PATTERN = re.compile(
    r"\s+"
)

COMPREHENSION_PATTERN = re.compile(
    r"^理解度\s*(\d+%?)"
)

DANGER_PATTERN = re.compile(
    r"^危険度\s*([0-9]/[0-9]|可変|未定.*)"
)


# =============================================================================
# 無視する行
# =============================================================================

IGNORE_LINE_PATTERNS = [
    re.compile(r"^この記事は"),
    re.compile(r"^↑"),
    re.compile(r"^※"),
    re.compile(r"^もし、?あなた"),
    re.compile(r"^現在、?このレベル"),
    re.compile(r"^これらの記述は"),
]


# =============================================================================
# 接続先Level検出
# =============================================================================

TARGET_PATTERN = re.compile(
    r"(?:"
    # The系
    r"The\s+Backrooms\s+Expansion\s+City(?:\s*η)?|"
    r"The\s+Lucid\s+Dream(?:\s*η)?|"
    r"The\s+Hide\s+and\s+Seek(?:\s*η)?|"
    r"The\s+Campaigner\s+Corridor(?:\s*η)?|"
    r"The\s+Monochrome\s+Room(?:\s*η)?|"
    r"The\s+Party\s+Rooms|"
    r"The\s+Play\s+Rooms|"
    r"The\s+Manila\s+Room|"
    r"The\s+Bathrooms(?:\s*η)?|"
    r"The\s+Frontrooms|"
    r"The\s+Whiteout|"
    r"The\s+Express(?:\s*η)?|"
    r"The\s+Exit\s+hub(?:\s*η)?|"
    r"The\s+Metro(?:\s*η)?|"
    r"The\s+Err0r(?:\s*η)?|"
    r"The\s+Void(?:\s+City(?:\s*η)?)?|"
    r"The\s+Hub(?:\s*η)?|"
    r"The\s+End|"

    # 海外支部・特殊プレフィックス
    r"Level\s+UA-[-0-9\(\)]+|"
    r"Level\s+PL-[-0-9]+|"
    r"Level\s+ZH\s*[-0-9]+|"
    r"Level\s+C-[-0-9]+|"

    # 特殊な英語名
    r"Level\s+Electric\s+Light(?:\s*η)?|"
    r"Level\s+Snow\s+Globe\s+[θ\w]+(?:\s*η)?|"
    r"Level\s+White(?:\s+Building)?(?:\s*η)?|"
    r"Level\s+A\s+Familiar\s+Sight(?:\s*η)?|"
    r"Level\s+(?:Fun|Fog|Fantasy|START|UP|LIQUID|"
    r"Bangs|Hell|Growth|Monochrome|UNDERGROUND|Level)"
    r"(?:\s*η)?|"

    # 一般Level番号
    r"Level\s+[-0-9A-Za-z.📦🥞🍔🍕🥙仝]+"
    r"(?:\s*η)?(?:\s*\(\d+\))?"
    r")",
    re.IGNORECASE,
)

ALL_LEVEL_KEYWORDS = (
    "全ての階層",
    "すべての階層",
    "どのレベル"
)


# =============================================================================
# ヘルパー
# =============================================================================

def clean_level_name(name: str) -> str:
    """
    Level名の空白・大文字小文字・記号表記を正規化する。
    """

    name = WS_PATTERN.sub(
        " ",
        name
    ).strip()

    if name.lower().startswith("level "):
        name = "Level " + name[6:]

    elif name.lower().startswith("the "):
        name = "The " + name[4:]

    # Level 46η -> Level 46 η
    name = re.sub(
        r"(\d+)(η)",
        r"\1 \2",
        name
    )

    # Level ZH75 -> Level ZH 75
    name = re.sub(
        r"^Level\s+ZH\s*(\d+)",
        r"Level ZH \1",
        name,
        flags=re.IGNORECASE
    )

    return name


def is_valid_target(name: str) -> bool:
    """
    不完全なLevel名を除外する。
    """

    name_clean = name.strip()

    if (
        name_clean.endswith("-")
        or name_clean.endswith("!")
        or name_clean in (
            "Level",
            "level",
            "THE",
            "The"
        )
    ):
        return False

    return True


# =============================================================================
# ネットワーク抽出
# =============================================================================

def process_backrooms_data():
    """
    extracted_fandom_levels_all.txt から
    ノード・エッジ情報を抽出してCSVに保存する。

    Returns
    -------
    bool
        成功時 True、入力ファイルがない場合などは False。
    """

    unique_edges = {}
    nodes_info = {}

    current_level = None
    current_section_type = None

    # =========================================================================
    # 入力
    # =========================================================================

    try:

        with open(
            INPUT_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            for line in f:

                line = line.strip()

                if not line:
                    continue

                # -----------------------------------------------------------------
                # 1. Levelタイトル
                # -----------------------------------------------------------------

                title_match = LEVEL_TITLE_PATTERN.match(
                    line
                )

                if title_match:

                    current_level = clean_level_name(
                        title_match.group(1)
                    )

                    current_section_type = None

                    if current_level not in nodes_info:

                        nodes_info[current_level] = {
                            "Id": current_level,
                            "Label": current_level,
                            "Comprehension": "",
                            "Danger": "",
                        }

                    continue

                # -----------------------------------------------------------------
                # 2. 理解度・危険度
                # -----------------------------------------------------------------

                if (
                    current_level
                    and not current_section_type
                ):

                    comp_match = (
                        COMPREHENSION_PATTERN.match(
                            line
                        )
                    )

                    if comp_match:

                        nodes_info[current_level][
                            "Comprehension"
                        ] = comp_match.group(1).strip()

                        continue

                    danger_match = (
                        DANGER_PATTERN.match(
                            line
                        )
                    )

                    if danger_match:

                        nodes_info[current_level][
                            "Danger"
                        ] = danger_match.group(1).strip()

                        continue

                # -----------------------------------------------------------------
                # 3. セクション
                # -----------------------------------------------------------------

                sec_match = SECTION_PATTERN.match(
                    line
                )

                if sec_match:

                    sec_name = sec_match.group(1)

                    if "入口" in sec_name:

                        current_section_type = "Entrance"

                    elif "出口" in sec_name:

                        current_section_type = "Exit"

                    else:

                        current_section_type = None

                    continue

                # -----------------------------------------------------------------
                # 4. 入口・出口から接続を抽出
                # -----------------------------------------------------------------

                if (
                    current_section_type
                    and current_level
                ):

                    # ノイズ除外
                    if any(
                        pat.search(line)
                        for pat in IGNORE_LINE_PATTERNS
                    ):
                        continue

                    raw_matches = (
                        TARGET_PATTERN.findall(
                            line
                        )
                    )

                    found_levels = {
                        clean_level_name(level)
                        for level in raw_matches
                        if is_valid_target(level)
                    }

                    # 「全ての階層」など
                    if (
                        not found_levels
                        and any(
                            kw in line
                            for kw in ALL_LEVEL_KEYWORDS
                        )
                    ):

                        found_levels.add(
                            "All Levels (全ての階層)"
                        )

                    current_level_lower = (
                        current_level.lower()
                    )

                    for target in found_levels:

                        if target not in nodes_info:

                            nodes_info[target] = {
                                "Id": target,
                                "Label": target,
                                "Comprehension": "",
                                "Danger": "",
                            }

                        # 自己接続を除外
                        if (
                            target.lower()
                            == current_level_lower
                        ):
                            continue

                        # ---------------------------------------------------------
                        # Entrance
                        # ---------------------------------------------------------

                        if (
                            current_section_type
                            == "Entrance"
                        ):

                            src = target
                            tgt = current_level

                        # ---------------------------------------------------------
                        # Exit
                        # ---------------------------------------------------------

                        else:

                            src = current_level
                            tgt = target

                        identifier = (
                            src,
                            tgt,
                            line
                        )

                        if (
                            identifier
                            not in unique_edges
                        ):

                            unique_edges[identifier] = {
                                "Source": src,
                                "Target": tgt,
                                "Type": "Directed",
                                "Category":
                                    current_section_type,
                                "Condition": line
                            }

    except FileNotFoundError:

        print()
        print(
            f"【エラー】ファイル "
            f"'{INPUT_FILE}' が見つかりません。"
        )

        print(
            "先に情報データベースの更新を実行してください。"
        )

        return False

    except Exception as e:

        print()
        print(
            "【エラー】ネットワーク解析中に"
            "予期しないエラーが発生しました。"
        )

        print(
            f"{type(e).__name__}: {e}"
        )

        return False

    # =========================================================================
    # エッジCSV
    # =========================================================================

    edges_fieldnames = [
        "Source",
        "Target",
        "Type",
        "Category",
        "Condition"
    ]

    try:

        with open(
            EDGES_OUTPUT_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as csvfile:

            writer = csv.DictWriter(
                csvfile,
                fieldnames=edges_fieldnames
            )

            writer.writeheader()

            writer.writerows(
                unique_edges.values()
            )

        print()
        print(
            f"エッジ抽出完了: "
            f"{len(unique_edges)} 件"
        )

        print(
            f" -> '{EDGES_OUTPUT_FILE}'"
        )

    except IOError as e:

        print()
        print(
            "【エラー】エッジファイルの"
            "書き込みに失敗しました。"
        )

        print(
            f"詳細: {e}"
        )

        return False

    # =========================================================================
    # ノードCSV
    # =========================================================================

    nodes_fieldnames = [
        "Id",
        "Label",
        "Comprehension",
        "Danger"
    ]

    try:

        with open(
            NODES_OUTPUT_FILE,
            "w",
            newline="",
            encoding="utf-8"
        ) as csvfile:

            writer = csv.DictWriter(
                csvfile,
                fieldnames=nodes_fieldnames
            )

            writer.writeheader()

            writer.writerows(
                nodes_info.values()
            )

        print(
            f"ノード抽出完了: "
            f"{len(nodes_info)} 件"
        )

        print(
            f" -> '{NODES_OUTPUT_FILE}'"
        )

    except IOError as e:

        print()
        print(
            "【エラー】ノードファイルの"
            "書き込みに失敗しました。"
        )

        print(
            f"詳細: {e}"
        )

        return False

    # =========================================================================
    # 完了
    # =========================================================================

    print()
    print("=" * 70)
    print("ネットワークデータの更新が完了しました。")
    print(
        f"ノード数 : {len(nodes_info):,}"
    )
    print(
        f"エッジ数 : {len(unique_edges):,}"
    )
    print("=" * 70)

    return True