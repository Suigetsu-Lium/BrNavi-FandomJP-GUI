"""
Backrooms Navigation System - Fandom JP

Backrooms Fandom JP のローカルデータベースを
閲覧・検索・ナビゲーションするための統合システム。
"""

import os
import sys

# =============================================================================
# カレントディレクトリの強制固定
# (ショートカット等から起動された場合でも、作業場所をexeの場所に固定する)
# =============================================================================

if getattr(sys, "frozen", False):
    os.chdir(os.path.dirname(sys.executable))


from pathlib import Path


# =============================================================================
# モジュール
# =============================================================================

# HTMLダウンローダー
from modules.downloader import main as run_downloader

# 画像抽出・ダウンローダー
from modules.image_downloader import download_images

# HTML本文・情報データ抽出
from modules.extractor import extract_levels

# Levelネットワーク抽出
from modules.network import process_backrooms_data

# Levelルート検索
from modules.navigator import run_navigation

# ローカルデータベース
from modules.database import (
    browse_database,
    find_level_candidates,
    get_level_info,
    print_level_info,
)

# Levelブラウザー
from modules.browser import browse_level

# Master Database 更新モジュール
from modules.update_database import main as run_update_database


# =============================================================================
# 基本設定
# =============================================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
)


# =============================================================================
# 共通
# =============================================================================

def pause():
    """
    Enterキーが押されるまで待機する。
    """

    input(
        "\nEnterキーでメニューに戻ります..."
    )


# =============================================================================
# メインメニュー
# =============================================================================

def show_menu():
    """
    メインメニューを表示する。
    """

    print()
    print("=" * 80)

    print(
        "        Backrooms Navigation System - Fandom JP"
    )

    print("=" * 80)

    print()

    print(
        "[1] Levelを検索"
    )

    print(
        "[2] Level情報を見る"
    )

    print(
        "[3] ルートを検索"
    )

    print(
        "[4] ローカルデータベースを見る"
    )

    print(
        "[5] Fandom JPからHTMLをダウンロード"
    )

    print(
        "[6] 画像をダウンロード"
    )

    print(
        "[7] 情報データベースを更新 (ローカルHTML抽出)"
    )

    print(
        "[8] ネットワークを更新"
    )

    print(
        "[9] Master Database を更新"
    )

    print()

    print(
        "[0] 終了"
    )

    print()


# =============================================================================
# Level検索
# =============================================================================

def level_search():
    """
    Level Browserを起動する。
    """

    browse_level()


# =============================================================================
# Level情報
# =============================================================================

def level_info():
    """
    Master Database / ローカルデータから
    Level情報を検索・表示する。
    """

    print()
    print("=" * 80)

    print(
        "        Backrooms Fandom JP - Level Information"
    )

    print("=" * 80)

    while True:

        print()

        query = input(
            "▶ Level・ページ名を入力 "
            "(空欄で終了) : "
        ).strip()

        if not query:

            print()
            print(
                "Level情報を終了します。"
            )

            return

        # ---------------------------------------------------------------------
        # 候補検索
        # ---------------------------------------------------------------------

        candidates = (
            find_level_candidates(
                query
            )
        )

        if not candidates:

            print()
            print(
                f"❌ ページが見つかりません: {query}"
            )

            continue

        # ---------------------------------------------------------------------
        # Master DB候補
        # ---------------------------------------------------------------------

        if (
            candidates
            and isinstance(
                candidates[0],
                dict
            )
        ):

            if len(candidates) == 1:

                selected_page = (
                    candidates[0]
                )

            else:

                print()
                print(
                    "候補が複数あります。"
                )

                visible_count = min(
                    len(candidates),
                    30
                )

                for index, page in enumerate(
                    candidates[
                        :visible_count
                    ],
                    1
                ):

                    print(
                        f"  [{index}] "
                        f"{page['title']} "
                        f"[{page.get('language') or '-'}] "
                        f"[{page.get('category') or '-'}]"
                    )

                if len(candidates) > 30:

                    print(
                        f"  ... 他 "
                        f"{len(candidates) - 30} 件"
                    )

                selected_page = None

                while True:

                    choice = input(
                        "番号を入力してください"
                        "（Enterでキャンセル）: "
                    ).strip()

                    if not choice:

                        break

                    if not choice.isdigit():

                        print(
                            "数字を入力してください。"
                        )

                        continue

                    index = (
                        int(choice) - 1
                    )

                    if (
                        0
                        <= index
                        < visible_count
                    ):

                        selected_page = (
                            candidates[index]
                        )

                        break

                    print(
                        "範囲外の番号です。"
                    )

                if selected_page is None:

                    continue

            level_id = (
                selected_page["title"]
            )

        # ---------------------------------------------------------------------
        # Legacy候補
        # ---------------------------------------------------------------------

        else:

            if len(candidates) == 1:

                level_id = (
                    candidates[0]
                )

            else:

                print()
                print(
                    "候補が複数あります。"
                )

                visible_count = min(
                    len(candidates),
                    30
                )

                for index, candidate in enumerate(
                    candidates[:visible_count],
                    1
                ):

                    print(
                        f"  [{index}] "
                        f"{candidate}"
                    )

                if len(candidates) > 30:

                    print(
                        f"  ... 他 "
                        f"{len(candidates) - 30} 件"
                    )

                level_id = None

                while True:

                    choice = input(
                        "番号を入力してください"
                        "（Enterでキャンセル）: "
                    ).strip()

                    if not choice:

                        break

                    if not choice.isdigit():

                        print(
                            "数字を入力してください。"
                        )

                        continue

                    index = (
                        int(choice) - 1
                    )

                    if (
                        0
                        <= index
                        < visible_count
                    ):

                        level_id = (
                            candidates[index]
                        )

                        break

                    print(
                        "範囲外の番号です。"
                    )

                if level_id is None:

                    continue

        # ---------------------------------------------------------------------
        # 情報取得
        # ---------------------------------------------------------------------

        info = get_level_info(
            level_id
        )

        print_level_info(
            info
        )

        print()

        print(
            "EnterキーでLevel入力に戻ります。"
        )

        input()


# =============================================================================
# ルート検索
# =============================================================================

def route_search():
    """
    Level間のルート検索を実行する。
    """

    run_navigation()


# =============================================================================
# ローカルデータベース
# =============================================================================

def database_browser():
    """
    Master Database / ローカルデータベースを閲覧する。
    """

    browse_database()


# =============================================================================
# HTMLダウンロード
# =============================================================================

def html_download():
    """
    Master Database対応HTML Downloaderを起動する。
    """

    print()
    print("=" * 80)

    print(
        "【 Fandom JP HTML Downloader 】"
    )

    print("=" * 80)

    print()

    try:

        run_downloader()

    except KeyboardInterrupt:

        print()
        print(
            "HTML Downloaderを終了しました。"
        )

    except Exception as e:

        print()
        print("=" * 80)

        print(
            "HTML Downloader中にエラーが発生しました。"
        )

        print("=" * 80)

        print()

        print(
            f"{type(e).__name__}: {e}"
        )


# =============================================================================
# 画像抽出・ダウンロード
# =============================================================================

def image_download():
    """
    ローカルHTMLから画像を抽出してダウンロードする。
    """

    print()
    print(
        "【 Fandom JP 画像抽出＆ダウンロード 】"
    )

    print()

    # -------------------------------------------------------------------------
    # 開始Level
    # -------------------------------------------------------------------------

    while True:

        start_text = input(
            "▶ 抽出を開始する Level の数値 "
            "(例: 0): "
        ).strip()

        if not start_text:

            print(
                "画像ダウンロードをキャンセルしました。"
            )

            return

        try:

            start_level = int(
                start_text
            )

            break

        except ValueError:

            print(
                "⚠ 数字を入力してください。"
            )

    # -------------------------------------------------------------------------
    # 終了Level
    # -------------------------------------------------------------------------

    while True:

        end_text = input(
            "▶ 抽出を終了する Level の数値 "
            "(例: 199): "
        ).strip()

        if not end_text:

            print(
                "画像ダウンロードをキャンセルしました。"
            )

            return

        try:

            end_level = int(
                end_text
            )

            break

        except ValueError:

            print(
                "⚠ 数字を入力してください。"
            )

    # -------------------------------------------------------------------------
    # 範囲確認
    # -------------------------------------------------------------------------

    if start_level > end_level:

        print()
        print(
            "[注意] 開始数値が終了Levelより大きいため、"
            "順序を入れ替えて処理します。"
        )

        start_level, end_level = (
            end_level,
            start_level
        )

    # -------------------------------------------------------------------------
    # 実行
    # -------------------------------------------------------------------------

    print()

    print(
        f"Level {start_level} ～ Level {end_level} "
        "の画像抽出・ダウンロードを開始します。"
    )

    print()

    try:

        download_images(
            start_level,
            end_level
        )

    except Exception as e:

        print()
        print("=" * 80)

        print(
            "画像抽出・ダウンロード中に"
            "エラーが発生しました。"
        )

        print("=" * 80)

        print()

        print(
            f"{type(e).__name__}: {e}"
        )


# =============================================================================
# 情報データベース更新
# =============================================================================

def database_update():
    """
    ローカルHTMLからLevel本文・メタ情報・タグを抽出する。
    """

    print()
    print("【 情報データベース更新 (ローカルHTML抽出) 】")
    print()

    try:

        extract_levels(
            interactive=True
        )

    except KeyboardInterrupt:

        print()
        print("情報抽出をキャンセルしました。")

    except Exception as e:

        print()
        print("=" * 80)
        print("情報データベース更新中にエラーが発生しました。")
        print("=" * 80)
        print()
        print(f"{type(e).__name__}: {e}")


# =============================================================================
# ネットワーク更新
# =============================================================================

def network_update():
    """
    抽出済みLevelデータから接続ネットワークを生成する。
    """

    print()
    print(
        "【 ネットワーク更新 】"
    )

    print()

    print(
        "extracted_fandom_levels_all.txt から "
        "Level間の接続を解析します。"
    )

    print()

    try:

        process_backrooms_data()

    except Exception as e:

        print()
        print("=" * 80)

        print(
            "ネットワーク更新中に"
            "エラーが発生しました。"
        )

        print("=" * 80)

        print()

        print(
            f"{type(e).__name__}: {e}"
        )


# =============================================================================
# Master Database 更新
# =============================================================================

def master_database_update():
    """
    Fandom JP の一覧HTMLからデータベース・Level Listを一括更新する。
    """

    print()
    print("=" * 80)

    print(
        "【 Master Database 更新 】"
    )

    print("=" * 80)

    print()

    try:

        run_update_database()

    except KeyboardInterrupt:

        print()
        print(
            "Database Updateを終了しました。"
        )

    except Exception as e:

        print()
        print("=" * 80)

        print(
            "Database Update中にエラーが発生しました。"
        )

        print("=" * 80)

        print()

        print(
            f"{type(e).__name__}: {e}"
        )


# =============================================================================
# メイン
# =============================================================================

def main():

    print()
    print("=" * 80)

    print(
        "        Backrooms Navigation System - Fandom JP"
    )

    print("=" * 80)

    print()

    print(
        "システムを起動しています..."
    )

    print()

    print(
        f"実行フォルダ : {BASE_DIR}"
    )

    while True:

        show_menu()

        choice = input(
            "▶選択 : "
        ).strip()

        # ---------------------------------------------------------------------
        # 1. Level検索
        # ---------------------------------------------------------------------

        if choice == "1":

            level_search()

            pause()

        # ---------------------------------------------------------------------
        # 2. Level情報
        # ---------------------------------------------------------------------

        elif choice == "2":

            level_info()

            pause()

        # ---------------------------------------------------------------------
        # 3. ルート検索
        # ---------------------------------------------------------------------

        elif choice == "3":

            route_search()

            pause()

        # ---------------------------------------------------------------------
        # 4. ローカルDB
        # ---------------------------------------------------------------------

        elif choice == "4":

            database_browser()

            pause()

        # ---------------------------------------------------------------------
        # 5. HTML Downloader
        # ---------------------------------------------------------------------

        elif choice == "5":

            html_download()

            pause()

        # ---------------------------------------------------------------------
        # 6. 画像ダウンロード
        # ---------------------------------------------------------------------

        elif choice == "6":

            image_download()

            pause()

        # ---------------------------------------------------------------------
        # 7. 情報DB更新
        # ---------------------------------------------------------------------

        elif choice == "7":

            database_update()

            pause()

        # ---------------------------------------------------------------------
        # 8. ネットワーク更新
        # ---------------------------------------------------------------------

        elif choice == "8":

            network_update()

            pause()

        # ---------------------------------------------------------------------
        # 9. Master DB更新
        # ---------------------------------------------------------------------

        elif choice == "9":

            master_database_update()

            pause()

        # ---------------------------------------------------------------------
        # 0. 終了
        # ---------------------------------------------------------------------

        elif choice == "0":

            print()
            print(
                "Backrooms Navigation Systemを終了します。"
            )

            print()

            break

        # ---------------------------------------------------------------------
        # 無効入力
        # ---------------------------------------------------------------------

        else:

            print()
            print(
                "⚠ 無効な選択です。"
            )

            print(
                "1～9または0を入力してください。"
            )

            pause()


# =============================================================================
# エントリーポイント
# =============================================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print()
        print(
            "Ctrl+Cが押されたため、"
            "システムを終了します。"
        )

        print()

    except Exception as e:

        print()
        print("=" * 80)

        print(
            "予期しないエラーが発生しました。"
        )

        print("=" * 80)

        print()

        print(
            f"{type(e).__name__}: {e}"
        )

        print()

        input(
            "Enterキーで終了します..."
        )
