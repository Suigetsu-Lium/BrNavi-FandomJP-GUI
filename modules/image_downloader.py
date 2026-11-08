"""
Backrooms Navigation System - Fandom JP
Image Extractor / Downloader Module

ローカル保存されたFandom JPのHTMLから画像を抽出し、
画像をdownloaded_imagesへ保存する。
"""

import os
import re
import time
from urllib.parse import unquote

from bs4 import BeautifulSoup
import requests


# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
    start_level,
    end_level
):
    """
    指定Level範囲のローカルHTMLから画像を抽出し、
    Fandom JPから画像をダウンロードする。
    """

    # -------------------------------------------------------------------------
    # 範囲調整
    # -------------------------------------------------------------------------

    if start_level > end_level:

        print()
        print(
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

    if not os.path.exists(HTML_DIR):

        print()
        print(
            f"❌ エラー: HTMLフォルダ "
            f"'{HTML_DIR}' が見つかりません。"
        )

        print(
            "先にHTMLダウンロードを実行してください。"
        )

        return False

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

    print()
    print(
        "🔍 wiki_list.txt を読み込んで"
        "マッピング情報を構築中..."
    )

    meta_title_map = load_meta_titles(
        LIST_FILE_PATH
    )

    print(
        f"   -> {len(meta_title_map)} 件の"
        "タイトル情報を登録しました。"
    )

    # -------------------------------------------------------------------------
    # HTMLファイル取得
    # -------------------------------------------------------------------------

    html_files = []

    for filename in os.listdir(HTML_DIR):

        if not filename.endswith(".html"):
            continue

        level_num = get_level_number(
            filename
        )

        if (
            level_num != 999999
            and start_level <= level_num <= end_level
        ):

            html_files.append(filename)

    html_files.sort(
        key=get_level_number
    )

    total_files = len(html_files)

    # -------------------------------------------------------------------------
    # 対象HTMLなし
    # -------------------------------------------------------------------------

    if total_files == 0:

        print()
        print(
            f"❌ 指定された範囲 "
            f"(Level {start_level} ～ Level {end_level}) "
            "のHTMLファイルが見つかりません。"
        )

        return False

    print()
    print(
        f"📁 {total_files} 件のHTMLファイルから"
        "画像を順番に検索します..."
    )
    print()

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

    for i, filename in enumerate(
        html_files
    ):

        filepath = os.path.join(
            HTML_DIR,
            filename
        )

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

            print(
                f"[{i + 1}/{total_files}] "
                f"読み込みエラー: {filename} - {e}"
            )

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

        if not content_area:

            print(
                f"[{i + 1}/{total_files}] "
                f"スキップ: {level_str} "
                "(本文領域なし)"
            )

            continue

        # ---------------------------------------------------------------------
        # 画像タグ取得
        # ---------------------------------------------------------------------

        img_tags = content_area.find_all(
            "img"
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

            print(
                f"[{i + 1}/{total_files}] "
                f"画像なし: {level_str}"
            )

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

        print(
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
            # 保存ファイル名
            # ---------------------------------------------------------------

            if safe_meta_title:

                new_img_filename = (
                    f"{level_str} - "
                    f"{safe_meta_title} - "
                    f"image{image_counter}."
                    f"{extension}"
                )

            else:

                new_img_filename = (
                    f"{level_str} - "
                    f"image{image_counter}."
                    f"{extension}"
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

                image_counter += 1
                continue

            # ---------------------------------------------------------------
            # ダウンロード
            # ---------------------------------------------------------------

            try:

                response = requests.get(
                    img_url,
                    headers=HEADERS,
                    timeout=15
                )

                # -----------------------------------------------------------
                # raw_url fallback
                # -----------------------------------------------------------

                if response.status_code != 200:

                    response = requests.get(
                        raw_url,
                        headers=HEADERS,
                        timeout=15
                    )

                # -----------------------------------------------------------
                # URL抽出 fallback
                # -----------------------------------------------------------

                if response.status_code != 200:

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

                else:

                    print(
                        f"    ⚠️ 最終エラー "
                        f"({response.status_code}): "
                        f"{new_img_filename}"
                    )

            except Exception as e:

                print(
                    f"    ❌ 通信エラー: "
                    f"{new_img_filename} - {e}"
                )

            image_counter += 1

    # -------------------------------------------------------------------------
    # 完了
    # -------------------------------------------------------------------------

    print()
    print("=" * 50)
    print(
        f"【完了】Level {start_level} ～ "
        f"{end_level} の画像抽出・"
        "ダウンロードが完了しました。"
    )
    print(
        f"保存先: {OUTPUT_IMG_DIR}/"
    )
    print("=" * 50)

    return True