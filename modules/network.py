"""
Backrooms Navigation System - Fandom JP
Network Extraction Module

downloaded_fandom_html/ の各記事HTMLから
Level間の接続情報を解析し、

    backrooms_edges.csv
    backrooms_nodes.csv

を生成する。
"""

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from modules.route_schema import (
    NODE_FIELDS, EDGE_FIELDS, EVIDENCE_FIELDS,
    NODE_FILENAME, EDGE_FILENAME, EVIDENCE_FILENAME,
    validate_dataset,
)

from modules.operation_context import OperationCancelled
from modules.runtime_paths import get_application_dir
from modules.route_analysis import classify_route_certainty, classify_route_node, extract_route_mentions, extract_explicit_transitions
from modules.extractor import (
    SUMMARY_CSV,
    determine_level_information,
    extract_danger,
    extract_article_text,
    extract_page_title_from_html,
    extract_url_from_html,
    extract_meta_info_from_html,
    extract_understanding,
)

# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = str(get_application_dir())

HTML_DIR = os.path.join(BASE_DIR, "downloaded_fandom_html")

EDGES_OUTPUT_FILE = os.path.join(BASE_DIR, EDGE_FILENAME)
NODES_OUTPUT_FILE = os.path.join(BASE_DIR, NODE_FILENAME)
EVIDENCE_OUTPUT_FILE = os.path.join(BASE_DIR, EVIDENCE_FILENAME)


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
    r"^(?:理解度|Comprehension|Understanding)\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
)

DANGER_PATTERN = re.compile(
    r"^(?:危険度|生存難易度|Survival\s+Difficulty|Danger)\s*[:：]?\s*(.+)$",
    re.IGNORECASE,
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

def _write_csv_staging_file(output_path, fieldnames, rows):
    output_dir = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(output_dir, exist_ok=True)
    fd, staging_path = tempfile.mkstemp(
        prefix=".brnavi-network-",
        suffix=".tmp",
        dir=output_dir,
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
    except Exception:
        os.unlink(staging_path)
        raise
    return staging_path


def _replace_csv_outputs(staged_outputs):
    backups = {}
    replaced = []
    completed = False
    preserve_backups = False
    try:
        for target, staging_path in staged_outputs:
            if os.path.exists(target):
                fd, backup_path = tempfile.mkstemp(
                    prefix=".brnavi-network-backup-",
                    dir=os.path.dirname(os.path.abspath(target)),
                )
                os.close(fd)
                backups[target] = backup_path
                shutil.copy2(target, backup_path)
            else:
                backups[target] = None

        for target, staging_path in staged_outputs:
            os.replace(staging_path, target)
            replaced.append(target)
        completed = True
    except Exception:
        rollback_errors = []
        for target in reversed(replaced):
            backup_path = backups.get(target)
            try:
                if backup_path:
                    os.replace(backup_path, target)
                    backups[target] = None
                elif os.path.exists(target):
                    os.unlink(target)
            except OSError as rollback_error:
                rollback_errors.append(f"{target}: {rollback_error}")
        if rollback_errors:
            preserve_backups = True
            raise OSError(
                "CSV replacement failed and rollback was incomplete; retained backup files: "
                + "; ".join(rollback_errors)
            )
        raise
    finally:
        for _, staging_path in staged_outputs:
            if os.path.exists(staging_path):
                os.unlink(staging_path)
        if completed or not preserve_backups:
            for backup_path in backups.values():
                if backup_path and os.path.exists(backup_path):
                    os.unlink(backup_path)


def _metadata_key(value):
    return re.sub(r"[\s_]+", "", unicodedata.normalize("NFKC", str(value or "")).casefold())


def _stable_route_id(prefix, identity):
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def _node_identity(title, canonical_url=""):
    if canonical_url:
        parts = urlsplit(canonical_url.strip())
        normalized_url = urlunsplit((
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path.rstrip("/"),
            "",
            "",
        ))
        return _stable_route_id("url", normalized_url)
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(title or "")).strip()).casefold()
    return _stable_route_id("node", normalized)


def _load_extracted_metadata():
    metadata = {}
    try:
        with open(SUMMARY_CSV, "r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                item = {
                    "Comprehension": str(row.get("理解度", "") or "").strip(),
                    "Danger": str(row.get("危険度", "") or "").strip(),
                }
                for alias in (row.get("階層コード"), row.get("メタタイトル")):
                    key = _metadata_key(alias)
                    if key:
                        current = metadata.setdefault(key, {})
                        for field, value in item.items():
                            if value:
                                current[field] = value
    except (OSError, csv.Error, UnicodeError):
        pass
    return metadata


def process_backrooms_data(context=None):
    """
    保存済みHTMLからNodes / Edges / Evidenceの3ファイルを生成する。

    3ファイルをメモリ上とステージング後の両方で検証し、検証完了後に
    バックアップ付きでまとめて置換する。旧形式CSVは自動移行・上書きしない。

    Returns
    -------
    bool
        成功時 True、入力ファイルがない場合などは False。
    """

    unique_edges = {}
    nodes_info = {}
    page_urls = {}
    extracted_metadata = _load_extracted_metadata()
    log = context.log if context else print

    # Never overwrite a legacy or unrecognized existing schema.
    legacy_headers = {
        ("Id", "Label", "Comprehension", "Danger"),
        ("Source", "Target", "Type", "Category", "Condition", "Certainty",
         "SourceKind", "TargetKind", "Evidence"),
    }
    expected_headers = {
        NODES_OUTPUT_FILE: NODE_FIELDS,
        EDGES_OUTPUT_FILE: EDGE_FIELDS,
        EVIDENCE_OUTPUT_FILE: EVIDENCE_FIELDS,
    }
    for output_path, expected_fields in expected_headers.items():
        if os.path.isfile(output_path):
            try:
                with open(output_path, "r", encoding="utf-8-sig", newline="") as stream:
                    header = tuple(next(csv.reader(stream), ()))
            except (OSError, csv.Error, UnicodeError) as error:
                log(f"【エラー】既存経路CSVを確認できません: {error}")
                return False
            if header in legacy_headers:
                log(f"【エラー】旧形式CSVを検出しました: {os.path.basename(output_path)}")
                log("自動移行・上書きは行いません。旧形式の移行は別ディレクトリへの明示的な操作として実施してください。")
                return False
            if header != tuple(expected_fields):
                log(f"【エラー】未対応または破損した経路CSVヘッダーです: {os.path.basename(output_path)}")
                log("既存ファイルは変更していません。ファイルの内容と形式を確認してください。")
                return False

    html_files = []
    if os.path.isdir(HTML_DIR):
        html_files = sorted(
            (
                os.path.join(HTML_DIR, name)
                for name in os.listdir(HTML_DIR)
                if name.lower().endswith((".html", ".htm"))
                and os.path.isfile(os.path.join(HTML_DIR, name))
            ),
            key=lambda path: os.path.basename(path).casefold(),
        )
    if not html_files:
        log(f"【エラー】解析対象のHTMLがありません: {HTML_DIR}")
        log("「HTML取得・データ抽出」タブからLevel記事を保存してから再実行してください。")
        return False

    log(f"保存HTMLを直接解析します: {len(html_files)} 件")
    parsed_pages = 0
    current_level = None
    current_article_id = ""
    current_section_type = None

    # =========================================================================
    # 入力
    # =========================================================================

    try:

        for page_index, html_path in enumerate(html_files, 1):
            if context:
                context.check_cancelled()
                context.progress(page_index - 1, len(html_files), "保存HTMLを解析中")
            try:
                with open(html_path, "r", encoding="utf-8-sig", errors="replace") as html_file:
                    html_text = html_file.read()
                article_text = extract_article_text(html_text)
                if not article_text.strip():
                    log(f"本文を抽出できないためスキップ: {os.path.basename(html_path)}", "warning")
                    continue
                page_title = extract_page_title_from_html(html_text)
                level_title, level_code, _ = determine_level_information(
                    article_text, page_title, os.path.basename(html_path)
                )
                if not level_title or not level_code:
                    continue
                current_level = clean_level_name(level_code)
                canonical_url = (
                    extract_url_from_html(html_text)
                    or extract_meta_info_from_html(html_text).get("url", "")
                )
                previous_url = page_urls.get(current_level, "")
                if (previous_url and canonical_url
                        and _node_identity(current_level, previous_url)
                        != _node_identity(current_level, canonical_url)):
                    log(
                        f"【エラー】同じLevel識別子に異なる記事URLが対応しています: "
                        f"{current_level}。曖昧な記事統合を避けるため更新を中止します。"
                    )
                    return False
                if canonical_url:
                    page_urls[current_level] = canonical_url
                current_article_id = current_level
                current_section_type = None
                current_section_certainty = "Unspecified"
                page_node = nodes_info.setdefault(current_level, {
                    "Id": current_level,
                    "Label": level_title,
                    "Comprehension": "",
                    "Danger": "",
                })
                page_node["Label"] = level_title or page_node.get("Label") or current_level
                comprehension = extract_understanding(article_text)[0]
                danger = extract_danger(article_text)[0]
                if comprehension:
                    page_node["Comprehension"] = comprehension
                if danger:
                    page_node["Danger"] = danger
                # 旧パーサーはTXT内のLevel見出しを開始点にしていた。
                # HTML本文には記事タイトルが含まれないため、抽出したページ自身の
                # タイトルを先頭に補って同じセクション解析へ渡す。
                normalized_title_line = level_title.rstrip(":：") + ":"
                page_lines = [normalized_title_line, *article_text.splitlines()]
                parsed_pages += 1
            except OperationCancelled:
                raise
            except Exception as error:
                log(f"HTML解析をスキップ: {os.path.basename(html_path)} ({type(error).__name__}: {error})", "warning")
                continue

            for line in page_lines:
                if context:
                    context.check_cancelled()
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
                    current_section_certainty = "Unspecified"

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

                    entrance_word = chr(0x5165) + chr(0x53e3)
                    exit_word = chr(0x51fa) + chr(0x53e3)
                    if entrance_word in sec_name:
                        current_section_type = "Entrance"
                        current_section_certainty = classify_route_certainty(sec_name)
                    elif exit_word in sec_name:
                        current_section_type = "Exit"
                        current_section_certainty = classify_route_certainty(sec_name)
                    else:
                        current_section_type = None
                        current_section_certainty = "Unspecified"

                    line = line[sec_match.end():].strip()
                    if not line:
                        continue

                # -----------------------------------------------------------------
                # 4. 入口・出口から接続を抽出
                # -----------------------------------------------------------------

                if (
                    current_section_type
                    and current_article_id
                ):

                    # ノイズ除外
                    if any(
                        pat.search(line)
                        for pat in IGNORE_LINE_PATTERNS
                    ):
                        continue

                    route_mentions = extract_route_mentions(line)
                    found_levels = {
                        target for target, _start, _end, _kind in route_mentions
                    }
                    legacy_targets = {
                        clean_level_name(target)
                        for target in TARGET_PATTERN.findall(line)
                        if is_valid_target(target)
                    }
                    legacy_targets.discard("The Frontrooms")
                    found_levels.update(legacy_targets)
                    if not found_levels and any(
                        keyword in line for keyword in ALL_LEVEL_KEYWORDS
                    ):
                        found_levels.add("All Levels (" + "".join(chr(code) for code in (0x5168, 0x3066, 0x306e, 0x968e, 0x5c64)) + ")")

                    explicit_transitions = extract_explicit_transitions(line)
                    if explicit_transitions:
                        edge_pairs = explicit_transitions
                    elif current_section_type == "Entrance":
                        edge_pairs = [(target, current_article_id) for target in found_levels]
                    else:
                        edge_pairs = [(current_article_id, target) for target in found_levels]

                    for src, tgt in edge_pairs:
                        src = clean_level_name(src)
                        tgt = clean_level_name(tgt)
                        if not src or not tgt or src.casefold() == tgt.casefold():
                            continue
                        for node_id in (src, tgt):
                            nodes_info.setdefault(node_id, {
                                "Id": node_id,
                                "Label": node_id,
                                "Comprehension": "",
                                "Danger": "",
                            })
                        identifier = (
                            src, tgt, current_article_id, current_section_type,
                            current_section_certainty, line,
                        )
                        if identifier not in unique_edges:
                            unique_edges[identifier] = {
                                "Source": src,
                                "Target": tgt,
                                "Article": current_article_id,
                                "Relation_Type": current_section_type,
                                "Raw_Text": line,
                                "Certainty": current_section_certainty,
                                "Extract_Method": "ExplicitTransition" if explicit_transitions else "SectionInference",
                            }

    except OperationCancelled:
        raise
    except Exception as e:

        log()
        log(
            "【エラー】ネットワーク解析中に"
            "予期しないエラーが発生しました。"
        )

        log(
            f"{type(e).__name__}: {e}"
        )

        return False

    if not nodes_info or not unique_edges:
        log("【エラー】保存HTMLからLevelノードと接続エッジを抽出できませんでした。")
        log(f"本文を解析したLevelページ: {parsed_pages} 件")
        log("既存の接続CSVは変更していません。HTML取得元と記事本文を確認してください。")
        return False

    for node_id, item in nodes_info.items():
        for alias in (node_id, item.get("Label")):
            metadata = extracted_metadata.get(_metadata_key(alias), {})
            for field in ("Comprehension", "Danger"):
                if not item.get(field) and metadata.get(field):
                    item[field] = metadata[field]

    # Canonical node IDs are URL-based for saved article pages and otherwise
    # based on conservative normalized identity. No fuzzy title merging occurs.
    node_id_by_title = {}
    nodes_rows = []
    for title, item in nodes_info.items():
        canonical_url = page_urls.get(title, "")
        node_id = _node_identity(title, canonical_url)
        node_id_by_title[title] = node_id
        nodes_rows.append({
            "Node_ID": node_id,
            "Canonical_Title": item.get("Label") or title,
            "Canonical_URL": canonical_url,
            "Aliases": json.dumps(
                [title] if title and title != (item.get("Label") or title) else [],
                ensure_ascii=False,
            ),
            "Comprehension": item.get("Comprehension", ""),
            "Danger": item.get("Danger", ""),
            "Node_Type": classify_route_node(title),
            "Data_Status": "NeedsReview",
        })

    edge_groups = {}
    evidence_rows = []
    for evidence in unique_edges.values():
        source_id = node_id_by_title.get(evidence["Source"])
        target_id = node_id_by_title.get(evidence["Target"])
        article_id = node_id_by_title.get(evidence["Article"])
        if not source_id or not target_id or not article_id or source_id == target_id:
            log("【エラー】抽出した根拠が既知ノードを参照していません。既存CSVは変更していません。")
            return False
        edge_key = (source_id, target_id, "Directed")
        edge_id = _stable_route_id("edge", "\n".join(edge_key))
        group = edge_groups.setdefault(edge_key, {
            "Edge_ID": edge_id,
            "Source_ID": source_id,
            "Target_ID": target_id,
            "Direction": "Directed",
            "Certainty": evidence["Certainty"] if evidence["Certainty"] in {"Confirmed", "Uncertain"} else "Unknown",
            "Availability": "Unknown",
            "Evidence_Type": "Unknown",
            "Probability": "Unknown",
            "Condition": evidence["Raw_Text"],
            "Review_Status": "NeedsReview",
        })
        certainty = evidence["Certainty"] if evidence["Certainty"] in {"Confirmed", "Uncertain"} else "Unknown"
        if group["Certainty"] != certainty:
            group["Certainty"] = "Unknown"
        if group["Condition"] != evidence["Raw_Text"]:
            group["Condition"] = ""
        evidence_identity = "\n".join((
            edge_id, article_id, evidence["Relation_Type"],
            evidence["Raw_Text"], evidence["Extract_Method"],
        ))
        evidence_rows.append({
            "Evidence_ID": _stable_route_id("evidence", evidence_identity),
            "Edge_ID": edge_id,
            "Article_ID": article_id,
            "Relation_Type": evidence["Relation_Type"],
            "Source_Raw": evidence["Source"],
            "Target_Raw": evidence["Target"],
            "Raw_Text": evidence["Raw_Text"],
            "Extract_Method": evidence["Extract_Method"],
            "Confidence": "",
            "Certainty": certainty,
            "Availability": "Unknown",
            "Evidence_Type": "Unknown",
            "Probability": "Unknown",
        })

    edges_rows = list(edge_groups.values())
    rows_by_name = {
        NODE_FILENAME: nodes_rows,
        EDGE_FILENAME: edges_rows,
        EVIDENCE_FILENAME: evidence_rows,
    }
    staging_paths = []
    try:
        if context:
            context.check_cancelled()
            context.progress(None, None, "3ファイルの整合性を検証中")
        validate_dataset(BASE_DIR, rows_by_name=rows_by_name)

        outputs = (
            (NODES_OUTPUT_FILE, NODE_FIELDS, nodes_rows),
            (EDGES_OUTPUT_FILE, EDGE_FIELDS, edges_rows),
            (EVIDENCE_OUTPUT_FILE, EVIDENCE_FIELDS, evidence_rows),
        )
        for target, fields, rows in outputs:
            staging_paths.append((target, _write_csv_staging_file(target, fields, rows)))

        # Re-read the staged bytes, not only the in-memory objects, before publish.
        staged_rows = {}
        for (target, fields, _rows), (_same_target, staged_path) in zip(outputs, staging_paths):
            with open(staged_path, "r", encoding="utf-8-sig", newline="") as stream:
                staged_rows[os.path.basename(target)] = list(csv.DictReader(stream))
        validate_dataset(BASE_DIR, rows_by_name=staged_rows)

        if context:
            context.check_cancelled()
            context.progress(None, None, "検証済み3ファイルを更新中")
        _replace_csv_outputs(staging_paths)
        staging_paths = []
    except OperationCancelled:
        raise
    except Exception as error:
        log("【エラー】ネットワークCSVの生成・検証・更新に失敗しました。")
        log(f"詳細: {type(error).__name__}: {error}")
        return False
    finally:
        for _target, staging_path in staging_paths:
            if os.path.exists(staging_path):
                os.unlink(staging_path)

    log()
    log(f"ノード: {len(nodes_rows)} 件 / エッジ: {len(edges_rows)} 件 / 根拠: {len(evidence_rows)} 件")
    log(f" -> {NODES_OUTPUT_FILE}")
    log(f" -> {EDGES_OUTPUT_FILE}")
    log(f" -> {EVIDENCE_OUTPUT_FILE}")
    log("ネットワークデータの更新が完了しました。")
    if context:
        context.progress(len(html_files), len(html_files), "完了")
    return True
