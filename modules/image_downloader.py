"""
Backrooms Navigation System - Fandom JP
Image Extractor / Downloader Module

ローカル保存されたFandom JPのHTMLから画像を抽出し、
画像をdownloaded_imagesへ保存する。
"""

import os
import re
import hashlib
import shutil
import time
from pathlib import Path
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup
import requests

from modules.operation_context import OperationCancelled
from modules.runtime_paths import get_application_dir

# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = str(get_application_dir())

LIST_FILE_PATH = os.path.join(
    BASE_DIR,
    "wiki_list.txt"
)

HTML_DIR = os.path.join(
    BASE_DIR,
    "downloaded_fandom_html"
)

OUTPUT_IMG_DIR = os.path.join(
    BASE_DIR,
    "downloaded_images"
)


# =============================================================================
# wiki_list.txt
# =============================================================================

def load_meta_titles(file_path):
    """
    wiki_list.txtからLevelとメタタイトルを読み込む。
    """

    meta_dict = {}

    if not os.path.exists(file_path):
        print(
            f"⚠️ 警告: '{file_path}' が見つかりません。"
        )
        print(
            "メタタイトルなしで進行します。"
        )
        return meta_dict

    with open(
        file_path,
        "r",
        encoding="utf-8",
        errors="ignore"
    ) as f:

        for line in f:

            line = line.strip()

            if not line.startswith("Level"):
                continue

            if ":" not in line:
                continue

            left, right = line.split(
                ":",
                1
            )

            left = left.strip()

            right = (
                right
                .strip()
                .strip('"')
                .strip("'")
                .strip()
            )

            match = re.search(
                r'Level\s+(-?\d+)\s*η(?:\s*\((.*?)\))?',
                left
            )

            if not match:
                continue

            lvl_num = match.group(1)
            branch = match.group(2)

            dict_key = (
                f"{lvl_num}_{branch}"
                if branch
                else f"{lvl_num}"
            )

            meta_dict[dict_key] = right

    return meta_dict


# =============================================================================
# 画像URL解析
# =============================================================================

def parse_image_info(url):
    """
    画像URLからダウンロードURLとファイル名を取得する。
    """

    if not url:
        return None, None

    if url.startswith("data:image"):
        return None, None

    match = re.search(
        r'/([^/]+\.(?:png|jpg|jpeg|gif|webp))',
        url,
        re.IGNORECASE
    )

    if not match:
        return None, None

    filename = unquote(
        match.group(1)
    )

    download_url = re.sub(
        r'(/revision/latest)[^?]*',
        r'\1',
        url
    )

    return download_url, filename


# =============================================================================
# Level情報
# =============================================================================

def extract_saved_page_url(html_content):
    """保存HTMLの管理メタデータから元ページURLを取得する。"""
    match = re.search(r"BRNAVI_META_INFO[^>]*?URL=(https?://[^\s|*<>]+)", html_content, re.IGNORECASE)
    return match.group(1).strip().rstrip(" -*>") if match else ""

def page_image_filename(page_url, image_index, extension):
    """Return a stable image filename scoped to the exact source article URL."""
    normalized_url = str(page_url or "").strip().rstrip("/")
    if not normalized_url:
        raise ValueError("記事URLがありません。")
    index = int(image_index)
    if index < 1:
        raise ValueError("画像番号は1以上である必要があります。")
    suffix = str(extension or "jpg").lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,8}", suffix):
        suffix = "jpg"
    page_key = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]
    return f"page_{page_key}_image{index}.{suffix}"


def plan_legacy_image_renames(html_dir=None, image_dir=None, meta_title_map=None):
    """Plan only unambiguous legacy-image renames; never modify files."""
    html_root = Path(html_dir or HTML_DIR)
    image_root = Path(image_dir or OUTPUT_IMG_DIR)
    if not html_root.is_dir() or not image_root.is_dir():
        return {"items": [], "skipped": 0}

    if meta_title_map is None:
        meta_title_map = load_meta_titles(LIST_FILE_PATH) if Path(LIST_FILE_PATH).is_file() else {}

    image_files = [
        path for path in image_root.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
    ]
    proposed = []
    skipped = 0

    for html_path in sorted(html_root.iterdir(), key=lambda item: item.name.casefold()):
        if not html_path.is_file() or html_path.suffix.lower() not in {".html", ".htm"}:
            continue
        try:
            html_content = html_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            skipped += 1
            continue

        page_url = extract_saved_page_url(html_content)
        if not page_url:
            skipped += 1
            continue
        soup = BeautifulSoup(html_content, "html.parser")
        content_area = soup.find(class_="mw-parser-output")
        if content_area is None:
            skipped += 1
            continue

        dict_key, level_str = extract_level_info(html_path.name)
        meta_title = str(meta_title_map.get(dict_key, "") or "").strip() if dict_key else ""
        safe_meta_title = re.sub(r'[\\/*?:"<>|]', "_", meta_title)
        broad_prefix = f"{level_str} - "
        exact_prefix = f"{broad_prefix}{safe_meta_title} - " if safe_meta_title else ""

        seen_urls = set()
        image_index = 0
        for img in content_area.find_all("img"):
            raw_url = img.get("data-src") or img.get("src")
            download_url, img_filename = parse_image_info(raw_url)
            if not download_url or not img_filename or download_url in seen_urls:
                continue
            seen_urls.add(download_url)
            image_index += 1

            suffix = Path(img_filename).suffix.lower()
            if not suffix:
                suffix = ".jpg"
            pattern = re.compile(rf" - image{image_index}$", re.IGNORECASE)
            candidates = [
                path for path in image_files
                if path.stem.casefold().startswith(broad_prefix.casefold())
                and pattern.search(path.stem)
            ]
            exact_candidates = [
                path for path in candidates
                if exact_prefix and path.stem.casefold().startswith(exact_prefix.casefold())
            ]
            if len(exact_candidates) == 1:
                candidates = exact_candidates
            if not candidates:
                # No legacy-named file exists for this image. It may already
                # have been migrated to the URL-keyed format; this is not an
                # ambiguous legacy mapping and should not inflate skipped.
                continue
            if len(candidates) != 1:
                skipped += 1
                continue

            source = candidates[0]
            # Keep the existing file's extension: it describes the bytes actually saved.
            target = image_root / page_image_filename(page_url, image_index, source.suffix)
            if source == target or target.exists():
                skipped += 1
                continue
            proposed.append({"source": source, "target": target, "page_url": page_url})

    # A legacy file can be claimed by multiple saved HTML pages (e.g. duplicate
    # Level IDs). Such mappings are ambiguous and must remain untouched.
    source_counts = {}
    target_counts = {}
    for item in proposed:
        source_counts[item["source"]] = source_counts.get(item["source"], 0) + 1
        target_counts[item["target"]] = target_counts.get(item["target"], 0) + 1
    safe_items = []
    for item in proposed:
        if source_counts[item["source"]] != 1 or target_counts[item["target"]] != 1 or item["target"].exists():
            skipped += 1
            continue
        safe_items.append({
            "source": item["source"],
            "target": item["target"],
            "page_url": item["page_url"],
        })
    return {"items": safe_items, "skipped": skipped}


def rename_legacy_images(html_dir=None, image_dir=None, meta_title_map=None, context=None):
    """Apply the safe rename plan without overwriting any existing file."""
    plan = plan_legacy_image_renames(html_dir, image_dir, meta_title_map)
    renamed = 0
    skipped = int(plan["skipped"])
    failed = 0
    results = []
    total = len(plan["items"])
    for index, item in enumerate(plan["items"], 1):
        if context:
            context.check_cancelled()
            context.progress(index - 1, total, item["source"].name)
        source = item["source"]
        target = item["target"]
        try:
            if not source.is_file() or target.exists():
                skipped += 1
                continue
            source.rename(target)
            renamed += 1
            results.append({"source": source.name, "target": target.name})
            if context:
                context.log(f"画像をリネームしました: {source.name} → {target.name}")
        except OSError as error:
            failed += 1
            if context:
                context.log(f"画像のリネームに失敗: {source.name} ({error})", "error")
        if context:
            context.progress(index, total, target.name)
    return {
        "renamed": renamed,
        "skipped": skipped,
        "failed": failed,
        "items": results,
    }



def extract_level_info(filename):
    """
    HTMLファイル名からLevel番号・枝番を取得する。
    """

    match = re.search(
        r'Level_(-?\d+)_η(?:_\((.*?)\))?',
        filename
    )

    if match:

        lvl_num = match.group(1)
        branch = match.group(2)

        dict_key = (
            f"{lvl_num}_{branch}"
            if branch
            else f"{lvl_num}"
        )

        if branch:

            level_str = (
                f"Level {lvl_num} η ({branch})"
            )

        else:

            level_str = (
                f"Level {lvl_num} η"
            )

        return dict_key, level_str

    return None, filename.replace(
        ".html",
        ""
    )


def get_level_number(filename):
    """
    HTMLファイル名からLevel番号を取得する。
    """

    match = re.search(
        r'Level_(-?\d+)',
        filename
    )

    if match:
        return int(match.group(1))

    return 999999


# =============================================================================
# 画像抽出・ダウンロード
# =============================================================================

def download_images(
    start_level=None,
    end_level=None,
    html_files=None,
    context=None,
    return_details=False,
):
    """
    指定Level範囲のローカルHTMLから画像を抽出し、
    Fandom JPから画像をダウンロードする。
    """

    log = context.log if context else print
    details = {"downloaded": 0, "skipped": 0, "failed": 0}

    if html_files is None and (start_level is None or end_level is None):
        raise ValueError("Level範囲、または処理対象HTMLを指定してください。")

    # -------------------------------------------------------------------------
    # 範囲調整
    # -------------------------------------------------------------------------

    if html_files is None and start_level is not None and end_level is not None and start_level > end_level:

        log()
        log(
            "[注意] 開始数値が終了数値より大きいため、"
            "順序を入れ替えて処理します。"
        )

        start_level, end_level = (
            end_level,
            start_level
        )

    # -------------------------------------------------------------------------
    # HTMLフォルダ確認
    # -------------------------------------------------------------------------

    if html_files is None and not os.path.exists(HTML_DIR):

        log()
        log(
            f"❌ エラー: HTMLフォルダ "
            f"'{HTML_DIR}' が見つかりません。"
        )

        log(
            "先にHTMLダウンロードを実行してください。"
        )

        return details if return_details else False

    # -------------------------------------------------------------------------
    # 出力フォルダ作成
    # -------------------------------------------------------------------------

    os.makedirs(
        OUTPUT_IMG_DIR,
        exist_ok=True
    )

    # -------------------------------------------------------------------------
    # wiki_list.txt読み込み
    # -------------------------------------------------------------------------

    if html_files is None:
        log()
        log(
            "🔍 wiki_list.txt を読み込んで"
            "マッピング情報を構築中..."
        )

        meta_title_map = load_meta_titles(
            LIST_FILE_PATH
        )

        log(
            f"   -> {len(meta_title_map)} 件の"
            "タイトル情報を登録しました。"
        )
    else:
        meta_title_map = {}
        log("選択したHTML内の画像だけを処理します。")

    # -------------------------------------------------------------------------
    # HTMLファイル取得
    # -------------------------------------------------------------------------

    if html_files is None:
        selected_html_files = []
        for filename in os.listdir(HTML_DIR):
            if not filename.lower().endswith((".html", ".htm")):
                continue
            level_num = get_level_number(filename)
            if (
                level_num != 999999
                and start_level <= level_num <= end_level
            ):
                selected_html_files.append(os.path.join(HTML_DIR, filename))
        selected_html_files.sort(
            key=lambda path: get_level_number(os.path.basename(path))
        )
    else:
        selected_html_files = sorted(
            (
                os.fspath(path)
                for path in html_files
                if os.path.isfile(path)
                and os.path.splitext(os.fspath(path))[1].lower() in (".html", ".htm")
            ),
            key=lambda path: os.path.basename(path).casefold(),
        )

    total_files = len(selected_html_files)

    # -------------------------------------------------------------------------
    # 対象HTMLなし
    # -------------------------------------------------------------------------

    if total_files == 0:

        log()
        if html_files is None:
            log(
                f"❌ 指定された範囲 "
                f"(Level {start_level} ～ Level {end_level}) "
                "のHTMLファイルが見つかりません。"
            )
        else:
            log("選択されたHTMLファイルがありません。")

        return details if return_details else False

    log()
    log(
        f"📁 {total_files} 件のHTMLファイルから"
        "画像を順番に検索します..."
    )
    log()

    # -------------------------------------------------------------------------
    # HTTPヘッダー
    # -------------------------------------------------------------------------

    HEADERS = {
        "User-Agent":
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0.0.0 "
            "Safari/537.36",

        "Accept":
            "image/avif,image/webp,image/apng,"
            "image/svg+xml,image/*,*/*;q=0.8",

        "Referer":
            "https://backrooms.fandom.com/"
    }

    # -------------------------------------------------------------------------
    # HTMLごとの処理
    # -------------------------------------------------------------------------

    for i, filepath in enumerate(selected_html_files):
        if context:
            context.check_cancelled()
            context.progress(i, total_files, os.path.basename(filepath))
        filename = os.path.basename(filepath)

        dict_key, level_str = (
            extract_level_info(filename)
        )

        meta_title = (
            meta_title_map.get(
                dict_key,
                ""
            )
            if dict_key
            else ""
        )

        # ---------------------------------------------------------------------
        # HTML読み込み
        # ---------------------------------------------------------------------

        try:

            with open(
                filepath,
                "r",
                encoding="utf-8",
                errors="ignore"
            ) as f:

                html_content = f.read()

        except Exception as e:

            log(
                f"[{i + 1}/{total_files}] "
                f"読み込みエラー: {filename} - {e}"
            )

            details["failed"] += 1
            continue

        # ---------------------------------------------------------------------
        # BeautifulSoup
        # ---------------------------------------------------------------------

        soup = BeautifulSoup(
            html_content,
            "html.parser"
        )

        content_area = soup.find(
            class_="mw-parser-output"
        )
        source_page_url = extract_saved_page_url(html_content)

        if not content_area:

            log(
                f"[{i + 1}/{total_files}] "
                f"スキップ: {level_str} "
                "(本文領域なし)"
            )

            details["skipped"] += 1
            continue

        # ---------------------------------------------------------------------
        # 画像タグ取得
        # ---------------------------------------------------------------------

        # Fandom ? Infobox ? mw-parser-output ??????????
        # ???????????????????????: pi-image-thumbnail?
        # ????????????Infobox ????????????????
        img_tags = soup.select(
            ".portable-infobox img, .mw-parser-output img"
        )

        valid_images = []
        seen = set()

        for img in img_tags:

            raw_url = (
                img.get("data-src")
                or img.get("src")
            )

            download_url, img_filename = (
                parse_image_info(raw_url)
            )

            if not (
                download_url
                and img_filename
            ):
                continue

            if download_url in seen:
                continue

            seen.add(
                download_url
            )

            valid_images.append(
                (
                    download_url,
                    img_filename,
                    raw_url
                )
            )

        # ---------------------------------------------------------------------
        # 画像なし
        # ---------------------------------------------------------------------

        if not valid_images:

            log(
                f"[{i + 1}/{total_files}] "
                f"画像なし: {level_str}"
            )

            details["skipped"] += 1
            continue

        # ---------------------------------------------------------------------
        # 発見表示
        # ---------------------------------------------------------------------

        if meta_title:

            display_title = (
                f"{level_str} - "
                f"{meta_title}"
            )

        else:

            display_title = level_str

        log(
            f"[{i + 1}/{total_files}] "
            f"🖼️ {len(valid_images)} 枚の画像を発見: "
            f"{display_title}"
        )

        # ---------------------------------------------------------------------
        # 画像ダウンロード
        # ---------------------------------------------------------------------

        image_counter = 1

        for (
            img_url,
            img_filename,
            raw_url
        ) in valid_images:

            # ---------------------------------------------------------------
            # 拡張子
            # ---------------------------------------------------------------

            if "." in img_filename:

                extension = (
                    img_filename
                    .split(".")[-1]
                )

            else:

                extension = "jpg"

            extension = (
                extension
                .split("?")[0]
            )

            # ---------------------------------------------------------------
            # タイトルのファイル名安全化
            # ---------------------------------------------------------------

            safe_meta_title = re.sub(
                r'[\\/*?:"<>|]',
                "_",
                meta_title
            )

            # ---------------------------------------------------------------
            # 保存ファイル名：ページURLを一意キーにする
            # ---------------------------------------------------------------

            if source_page_url:
                new_img_filename = page_image_filename(
                    source_page_url,
                    image_counter,
                    extension,
                )
            elif safe_meta_title:
                new_img_filename = (
                    f"{level_str} - {safe_meta_title} - "
                    f"image{image_counter}.{extension}"
                )
            else:
                new_img_filename = (
                    f"{level_str} - image{image_counter}.{extension}"
                )

            img_save_path = os.path.join(
                OUTPUT_IMG_DIR,
                new_img_filename
            )

            # ---------------------------------------------------------------
            # 取得済み
            # ---------------------------------------------------------------

            if os.path.exists(
                img_save_path
            ):

                details["skipped"] += 1
                image_counter += 1
                continue

            # ---------------------------------------------------------------
            # ダウンロード
            # ---------------------------------------------------------------

            try:
                if context:
                    context.check_cancelled()

                # Offline HTML downloads store page images beside the HTML in a
                # *_assets directory and rewrite img src to a relative path.
                # Reuse that already-downloaded file instead of requesting the
                # relative path as if it were an HTTP URL.
                local_asset = None
                parsed_raw_url = raw_url.split("?", 1)[0]
                if not re.match(r"^https?://", parsed_raw_url, re.IGNORECASE):
                    candidate = (Path(filepath).parent / unquote(parsed_raw_url)).resolve()
                    html_root = Path(filepath).parent.resolve()
                    try:
                        candidate.relative_to(html_root)
                    except ValueError:
                        candidate = None
                    if candidate is not None and candidate.is_file():
                        local_asset = candidate

                if local_asset is not None:
                    shutil.copy2(local_asset, img_save_path)
                    details["downloaded"] += 1
                    image_counter += 1
                    continue

                request_url = img_url
                if source_page_url and not re.match(r"^https?://", request_url, re.IGNORECASE):
                    request_url = urljoin(source_page_url, request_url)

                response = requests.get(
                    request_url,
                    headers=HEADERS,
                    timeout=15
                )

                # -----------------------------------------------------------
                # raw_url fallback
                # -----------------------------------------------------------

                if response.status_code != 200:
                    if context:
                        context.check_cancelled()

                    fallback_url = (
                        urljoin(source_page_url, raw_url)
                        if source_page_url and not re.match(r"^https?://", raw_url, re.IGNORECASE)
                        else raw_url
                    )
                    response = requests.get(
                        fallback_url,
                        headers=HEADERS,
                        timeout=15
                    )

                # -----------------------------------------------------------
                # URL抽出 fallback
                # -----------------------------------------------------------

                if response.status_code != 200:
                    if context:
                        context.check_cancelled()

                    fallback_match = re.search(
                        r'(https?://.*?/[^/]+\.(?:png|jpg|jpeg|gif|webp))',
                        raw_url,
                        re.IGNORECASE
                    )

                    if fallback_match:

                        response = requests.get(
                            fallback_match.group(1),
                            headers=HEADERS,
                            timeout=15
                        )

                # -----------------------------------------------------------
                # 保存
                # -----------------------------------------------------------

                if response.status_code == 200:
                    if context:
                        context.check_cancelled()

                    with open(
                        img_save_path,
                        "wb"
                    ) as f:

                        f.write(
                            response.content
                        )

                    time.sleep(
                        0.5
                    )
                    details["downloaded"] += 1

                else:

                    log(
                        f"    ⚠️ 最終エラー "
                        f"({response.status_code}): "
                        f"{new_img_filename}"
                    )
                    details["failed"] += 1

            except OperationCancelled:
                raise
            except Exception as e:

                log(
                    f"    ❌ 通信エラー: "
                    f"{new_img_filename} - {e}"
                )
                details["failed"] += 1

            image_counter += 1

    # -------------------------------------------------------------------------
    # 完了
    # -------------------------------------------------------------------------

    log()
    log("=" * 50)
    if html_files is None:
        log(
            f"【完了】Level {start_level} ～ "
            f"{end_level} の画像抽出・"
            "ダウンロードが完了しました。"
        )
    else:
        log(f"選択したHTML {total_files}件の画像抽出が完了しました。")
    log(
        f"保存先: {OUTPUT_IMG_DIR}/"
    )
    log("=" * 50)

    if context:
        context.progress(total_files, total_files, "画像抽出完了")
    return details if return_details else True