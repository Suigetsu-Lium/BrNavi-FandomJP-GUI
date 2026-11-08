"""
Backrooms Navigation System - Fandom JP
Index Collector

Fandom JP の各種「一覧」ページを取得し、
ページ内のWiki記事リンクを抽出する。
"""

import os
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from curl_cffi import requests


# =============================================================================
# 基本パス
# =============================================================================

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INDEX_HTML_DIR = os.path.join(
    BASE_DIR,
    "index_html"
)

BASE_URL = "https://backrooms.fandom.com"


# =============================================================================
# 取得対象一覧
# =============================================================================

INDEX_PAGES = {
    "hierarchy": (
        "https://backrooms.fandom.com/ja/wiki/"
        "%E9%9A%8E%E5%B1%A4%E3%81%AE%E4%B8%80%E8%A6%A7"
    ),

    "translation": (
        "https://backrooms.fandom.com/ja/wiki/"
        "%E7%BF%BB%E8%A8%B3%E3%81%AE%E4%B8%80%E8%A6%A7"
    ),

    "normal_eta": (
        "https://backrooms.fandom.com/ja/wiki/"
        "%E9%80%9A%E5%B8%B8%E9%9A%8E%E5%B1%A4_%CE%B7_%E3%81%AE%E4%B8%80%E8%A6%A7"
    ),

    "anomalous": (
        "https://backrooms.fandom.com/ja/wiki/"
        "%E7%95%AA%E5%A4%96%E9%9A%8E%E5%B1%A4%E3%81%AE%E4%B8%80%E8%A6%A7"
    ),

    "sublevel": (
        "https://backrooms.fandom.com/ja/wiki/"
        "%E4%BA%9C%E9%9A%8E%E5%B1%A4%E3%81%AE%E4%B8%80%E8%A6%A7"
    ),
}


# =============================================================================
# 一覧HTML取得
# =============================================================================

def download_index(name, url):
    """
    一覧ページHTMLを取得して保存する。
    """

    os.makedirs(
        INDEX_HTML_DIR,
        exist_ok=True
    )

    file_path = os.path.join(
        INDEX_HTML_DIR,
        f"{name}.html"
    )

    print()
    print("=" * 60)
    print(f"一覧ページ取得: {name}")
    print(url)
    print("=" * 60)

    try:

        response = requests.get(
            url,
            impersonate="chrome",
            timeout=20
        )

        print(
            f"ステータスコード: {response.status_code}"
        )

        if response.status_code != 200:
            print("取得失敗")
            return None

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(response.text)

        print(
            f"保存完了: {file_path}"
        )

        return response.text

    except Exception as e:

        print(
            f"通信エラー: {e}"
        )

        return None


# =============================================================================
# リンク抽出
# =============================================================================

def extract_links(html):
    """
    HTMLからWiki記事らしきリンクを抽出する。
    """

    soup = BeautifulSoup(
        html,
        "html.parser"
    )

    results = []
    seen = set()

    for a in soup.find_all("a", href=True):

        title = a.get_text(
            " ",
            strip=True
        )

        href = a["href"]

        # Wikiページへのリンクだけを見る
        if "/ja/wiki/" not in href:
            continue

        url = urljoin(
            BASE_URL,
            href
        )

        key = (
            title,
            url
        )

        if key in seen:
            continue

        seen.add(key)

        results.append({
            "title": title,
            "url": url,
        })

    return results


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print("=" * 60)
    print(" Backrooms Fandom JP - Index Collector")
    print("=" * 60)

    for name, url in INDEX_PAGES.items():

        html = download_index(
            name,
            url
        )

        if html is None:
            continue

        links = extract_links(
            html
        )

        print()
        print(
            f"抽出リンク数: {len(links)}"
        )

        for i, item in enumerate(
            links[:30],
            start=1
        ):

            print(
                f"[{i}] "
                f"{item['title']} "
                f"-> "
                f"{item['url']}"
            )

        if len(links) > 30:

            print(
                f"... "
                f"（残り {len(links) - 30} 件）"
            )

        time.sleep(2)

    print()
    print("=" * 60)
    print("一覧ページ取得・リンク抽出終了")
    print("=" * 60)


if __name__ == "__main__":
    main()