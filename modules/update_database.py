"""
Backrooms Navigation System - Fandom JP
Database Update Orchestrator

Fandom JP の一覧HTMLから、データベース・検索用Level Listまでを
一括更新する。
"""

import os
import sys
import time
import traceback


# =============================================================================
# 基本パス設定
# =============================================================================

if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )

MODULES_DIR = os.path.join(
    BASE_DIR,
    "modules"
)

INDEX_HTML_DIR = os.path.join(
    BASE_DIR,
    "index_html"
)

PARSED_INDEX_DIR = os.path.join(
    BASE_DIR,
    "parsed_index"
)

LEVEL_LIST_DIR = os.path.join(
    BASE_DIR,
    "level_lists"
)


# =============================================================================
# modules を import path に追加
# =============================================================================

if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)


# =============================================================================
# モジュール読み込み
# =============================================================================

try:
    import index_collector
    import index_parser
    import classify_pages
    import translation_parser
    import translation_classifier
    import merge_databases
    import level_list_generator
except ImportError:
    from modules import index_collector
    from modules import index_parser
    from modules import classify_pages
    from modules import translation_parser
    from modules import translation_classifier
    from modules import merge_databases
    from modules import level_list_generator
except Exception as e:
    print()
    print("=" * 80)
    print("【エラー】モジュールの読み込みに失敗しました。")
    print("=" * 80)
    print(f"\nエラー内容: {e}\n")
    traceback.print_exc()
    raise e


# =============================================================================
# ファイル確認
# =============================================================================

def file_exists(path):
    return os.path.isfile(path)


def directory_exists(path):
    return os.path.isdir(path)


def check_index_html():
    required_files = [
        "hierarchy.html",
        "translation.html",
        "normal_eta.html",
        "anomalous.html",
        "sublevel.html",
    ]

    missing = []

    if not directory_exists(INDEX_HTML_DIR):
        missing = required_files
    else:
        for file_name in required_files:
            file_path = os.path.join(INDEX_HTML_DIR, file_name)
            if not file_exists(file_path):
                missing.append(file_name)

    if not missing:
        return True

    print()
    print("=" * 80)
    print("【エラー】必要な一覧HTMLがありません。")
    print("=" * 80)
    print(f"\n確認対象フォルダ:\n  {INDEX_HTML_DIR}\n")
    print("不足しているファイル:")
    for file_name in missing:
        print(f"  - {file_name}")
    print("\n[1] 全更新を選択すると一覧HTMLを取得できます。\n")

    return False


def show_database_status():
    print("\n【生成済みDB】\n")
    files = [
        "all_links.csv",
        "pages_database.csv",
        "pages_database_classified.csv",
        "translation_database.csv",
        "translation_database_classified.csv",
        "pages_master.csv",
    ]

    found = False
    for file_name in files:
        file_path = os.path.join(PARSED_INDEX_DIR, file_name)
        if os.path.isfile(file_path):
            found = True
            try:
                size = os.path.getsize(file_path)
                print(f"  ✓ {file_name} ({size:,} bytes)")
            except OSError:
                print(f"  ✓ {file_name}")

    if not found:
        print("  生成済みDBはありません。")


def run_step(number, name, function):
    print()
    print("=" * 80)
    print(f"[{number}] {name}")
    print("=" * 80)

    start_time = time.time()

    try:
        result = function()
        elapsed = time.time() - start_time

        if result is False:
            print(f"\n【FAILED】[{number}] {name}")
            print(f"処理時間: {elapsed:.1f} 秒\n")
            return False

        print(f"\n【SUCCESS】[{number}] {name}")
        print(f"処理時間: {elapsed:.1f} 秒")
        print("=" * 80)
        return True

    except Exception as e:
        elapsed = time.time() - start_time
        print("\n" + "=" * 80)
        print(f"【FAILED】[{number}] {name}")
        print(f"処理時間: {elapsed:.1f} 秒")
        print("=" * 80 + "\n")
        print(f"エラー種類: {type(e).__name__}")
        print(f"エラー内容: {e}\n")
        traceback.print_exc()
        return False


def show_menu():
    print()
    print("=" * 80)
    print(" Backrooms Navigation System - Fandom JP")
    print(" Database Update")
    print("=" * 80 + "\n")
    print("更新方法を選択してください。\n")
    print("[1] 全更新")
    print("    一覧HTMLを再取得 → DB再構築 → Level List生成\n")
    print("[2] HTMLを再取得せずDBだけ再構築")
    print("    現在のindex_html/を使用 → DB再構築 → Level List生成\n")
    print("[0] 終了\n")
    print("=" * 80)


def get_mode():
    while True:
        try:
            choice = input("▶選択: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n終了します。")
            return "0"

        if choice in {"0", "1", "2"}:
            return choice

        print("\n【エラー】1 / 2 / 0 のいずれかを入力してください。\n")


def build_steps(mode):
    steps = []
    if mode == "1":
        steps.append(("1/7", "Fandom JP 一覧HTML取得", index_collector.main))
        start_number = 2
        total_number = 7
    else:
        start_number = 1
        total_number = 6

    steps.append((f"{start_number}/{total_number}", "一覧HTML解析・ページDB作成", index_parser.main))
    steps.append((f"{start_number + 1}/{total_number}", "ページ分類", classify_pages.main))
    steps.append((f"{start_number + 2}/{total_number}", "翻訳一覧HTML解析", translation_parser.main))
    steps.append((f"{start_number + 3}/{total_number}", "翻訳記事分類", translation_classifier.main))
    steps.append((f"{start_number + 4}/{total_number}", "Master Database統合", merge_databases.main))
    steps.append((f"{start_number + 5}/{total_number}", "言語別Level List生成", level_list_generator.main))
    return steps


def show_selected_mode(mode):
    print("\n" + "=" * 80)
    if mode == "1":
        print("選択: [1] 全更新")
        print("一覧HTMLを再取得してからDBを再構築します。")
    elif mode == "2":
        print("選択: [2] HTMLを再取得せずDBだけ再構築")
        print("現在のindex_html/を使用します。")
    print("=" * 80)


def execute_update(mode):
    if mode == "2":
        if not check_index_html():
            return False

    steps = build_steps(mode)
    overall_start = time.time()
    completed = 0

    for number, name, function in steps:
        success = run_step(number, name, function)
        if not success:
            print("\n" + "=" * 80)
            print("データベース更新を中断しました。")
            print("=" * 80 + "\n")
            print(f"完了済み: {completed}/{len(steps)} ステップ\n")
            show_database_status()
            return False
        completed += 1

    elapsed = time.time() - overall_start
    print("\n\n" + "=" * 80)
    print(" ★ データベース更新完了 ★")
    print("=" * 80 + "\n")
    print(f"全ステップ完了: {completed}/{len(steps)}")
    print(f"総処理時間: {elapsed:.1f} 秒\n")
    print("更新された主なデータ:")
    if mode == "1":
        print(f"  {INDEX_HTML_DIR}")
    print(f"  {os.path.join(PARSED_INDEX_DIR, 'pages_master.csv')}")
    print(f"  {LEVEL_LIST_DIR}\n")
    show_database_status()
    print("\n" + "=" * 80)
    return True


def main():
    print("\n" + "=" * 80)
    print("Backrooms Navigation System - Fandom JP\nDatabase Update")
    print("=" * 80 + "\n")
    print(f"実行フォルダ: {BASE_DIR}")

    show_menu()
    mode = get_mode()

    if mode == "0":
        print("\n終了します。")
        return True

    show_selected_mode(mode)
    success = execute_update(mode)
    return success


update_database = main


if __name__ == "__main__":
    try:
        success = main()
        if not success:
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nCtrl+Cが押されたため、更新処理を終了します。")
        sys.exit(130)
    except Exception as e:
        print("\n" + "=" * 80 + "\n【致命的エラー】\n" + "=" * 80 + "\n")
        print(f"エラー種類: {type(e).__name__}")
        print(f"エラー内容: {e}\n")
        traceback.print_exc()
        sys.exit(1)
