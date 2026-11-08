"""
Backrooms Navigation System - Fandom JP
Level Browser Module

ローカル保存されたBackrooms Fandom JPのLevelを
ブラウザーのように探索する。

機能:
    ・Level検索
    ・本文閲覧
    ・画像閲覧
    ・接続Level一覧
    ・接続Levelへのジャンプ
    ・ルート検索
    ・戻る [B]
    ・進む [F]
    ・ホーム [H]
"""

from pathlib import Path
import os


# =============================================================================
# ローカルデータベース
# =============================================================================

from modules.database import (
    find_level_candidates,
    get_level_info,
    display_text_database,
)


# =============================================================================
# ネットワーク・ルート検索
# =============================================================================

from modules.navigator import (
    load_nodes,
    load_edges,
    find_shortest_distance,
    find_shortest_routes,
    find_node,
    print_route,
)


# =============================================================================
# 基本設定
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent

IMAGE_DIR = BASE_DIR / "downloaded_images"


# =============================================================================
# 画像検索
# =============================================================================

def find_level_images(level_id):
    """
    対象Levelのローカル画像を取得する。

    以下の両方に対応:
        Level 5 η - ...
        Level_5_η - ...
    """

    if not IMAGE_DIR.exists():
        return []

    images = []

    # 通常形式
    normal_prefix = level_id

    # HTMLダウンローダー形式
    normalized_prefix = level_id.replace(
        "Level ",
        "Level_",
        1
    ).replace(
        " η",
        "_η",
        1
    )

    for path in IMAGE_DIR.iterdir():

        if not path.is_file():
            continue

        if path.suffix.lower() not in {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
        }:
            continue

        if path.name.startswith(
            normal_prefix
        ):
            images.append(path)

        elif path.name.startswith(
            normalized_prefix
        ):
            images.append(path)

    # 重複除去
    images = list(
        dict.fromkeys(images)
    )

    images.sort(
        key=lambda path: path.name
    )

    return images


# =============================================================================
# 画像を開く
# =============================================================================

def open_image(image_path):
    """
    Windowsの既定アプリケーションで画像を開く。
    """

    try:

        os.startfile(
            str(image_path)
        )

        print()
        print(
            f"✅ 画像を開きました : "
            f"{image_path.name}"
        )

    except Exception as e:

        print()
        print(
            "❌ 画像を開けませんでした。"
        )

        print(
            f"{type(e).__name__}: {e}"
        )


# =============================================================================
# 画像一覧・画像ビューアー
# =============================================================================

def show_images(level_id):
    """
    対象Levelのローカル画像一覧を表示し、
    番号を指定して画像を既定アプリで開く。
    """

    images = find_level_images(
        level_id
    )

    print()
    print("=" * 80)
    print(
        f"【 画像 】 {level_id}"
    )
    print("=" * 80)

    if not images:

        print()
        print(
            "このLevelのローカル画像はありません。"
        )

        input(
            "\nEnterキーで戻ります..."
        )

        return

    print()
    print(
        f"画像数 : {len(images)} 枚"
    )
    print()

    for index, image in enumerate(
        images,
        1
    ):

        print(
            f"[{index}] {image.name}"
        )

    print()
    print(
        "画像番号を入力すると、"
        "Windowsの既定画像ビューアーで開きます。"
    )

    print(
        "EnterでLevel Browserに戻ります。"
    )

    while True:

        choice = input(
            "▶開く画像番号 : "
        ).strip()

        if not choice:
            return

        if not choice.isdigit():

            print(
                "数字を入力してください。"
            )

            continue

        index = int(
            choice
        ) - 1

        if not (
            0 <= index < len(images)
        ):

            print(
                "範囲外の番号です。"
            )

            continue

        open_image(
            images[index]
        )

        print()
        print(
            "別の画像を開く場合は番号を入力してください。"
        )

        print(
            "Enterで戻ります。"
        )


# =============================================================================
# 接続条件表示
# =============================================================================

def show_connection_detail(
    source,
    target,
    edge_info,
):
    """
    指定された接続の条件を表示する。
    """

    print()
    print("=" * 80)
    print(
        "【 接続情報 】"
    )
    print("=" * 80)

    print()
    print(
        f"{source} → {target}"
    )

    print()

    infos = edge_info.get(
        (source, target),
        []
    )

    if not infos:

        print(
            "接続条件の詳細はありません。"
        )

    elif len(infos) == 1:

        info = infos[0]

        if info["type"]:

            print(
                f"Type     : "
                f"{info['type']}"
            )

        if info["category"]:

            print(
                f"Category : "
                f"{info['category']}"
            )

        if info["condition"]:

            print(
                f"条件     : "
                f"{info['condition']}"
            )

    else:

        print(
            f"接続条件 : {len(infos)} 件"
        )

        for index, info in enumerate(
            infos,
            1
        ):

            print()
            print(
                f"[{index}]"
            )

            if info["type"]:

                print(
                    f"  Type     : "
                    f"{info['type']}"
                )

            if info["category"]:

                print(
                    f"  Category : "
                    f"{info['category']}"
                )

            if info["condition"]:

                print(
                    f"  条件     : "
                    f"{info['condition']}"
                )

    print()
    print("=" * 80)


# =============================================================================
# 接続先一覧
# =============================================================================

def show_connections(
    level_id,
    graph,
    edge_info,
    nodes,
):
    """
    対象Levelから直接移動できるLevelを表示する。

    戻り値:
        str  : 開くLevel ID
        None : 戻る
    """

    targets = graph.get(
        level_id,
        []
    )

    if not targets:

        print()
        print(
            f"{level_id} から直接移動できるLevelはありません。"
        )

        input(
            "\nEnterキーで戻ります..."
        )

        return None

    print()
    print("=" * 80)
    print(
        f"【 接続先 】 {level_id}"
    )
    print("=" * 80)

    for index, target in enumerate(
        targets,
        1
    ):

        info = nodes.get(
            target,
            {}
        )

        title = info.get(
            "title",
            ""
        )

        danger = info.get(
            "danger",
            ""
        )

        comprehension = info.get(
            "comprehension",
            ""
        )

        display = (
            f"[{index}] {target}"
        )

        if title:

            display += (
                f' "{title}"'
            )

        meta = []

        if danger:

            meta.append(
                f"危険度: {danger}"
            )

        if comprehension:

            meta.append(
                f"理解度: {comprehension}"
            )

        if meta:

            display += (
                f" ({', '.join(meta)})"
            )

        print(
            display
        )

    print("=" * 80)

    print()
    print(
        "番号を入力すると、そのLevelの操作を選択できます。"
    )

    print(
        "Enterで戻ります。"
    )

    while True:

        choice = input(
            "▶選択 : "
        ).strip()

        if not choice:
            return None

        if not choice.isdigit():

            print(
                "数字を入力してください。"
            )

            continue

        index = int(
            choice
        ) - 1

        if not (
            0 <= index < len(targets)
        ):

            print(
                "範囲外の番号です。"
            )

            continue

        target = targets[index]

        target_info = nodes.get(
            target,
            {}
        )

        print()
        print("=" * 80)

        if target_info.get(
            "title"
        ):

            print(
                f'{target} '
                f'"{target_info["title"]}"'
            )

        else:

            print(
                target
            )

        print("=" * 80)

        if target_info.get(
            "danger"
        ):

            print(
                f"危険度     : "
                f"{target_info['danger']}"
            )

        if target_info.get(
            "comprehension"
        ):

            print(
                f"理解度     : "
                f"{target_info['comprehension']}"
            )

        print()
        print(
            "[1] このLevelを開く"
        )

        print(
            "[2] 接続条件を見る"
        )

        print(
            "[0] 戻る"
        )

        print()

        action = input(
            "▶選択 : "
        ).strip()

        # ---------------------------------------------------------------------
        # Levelを開く
        # ---------------------------------------------------------------------

        if action == "1":

            return target

        # ---------------------------------------------------------------------
        # 接続条件
        # ---------------------------------------------------------------------

        elif action == "2":

            show_connection_detail(
                level_id,
                target,
                edge_info
            )

            input(
                "\nEnterキーで接続先一覧に戻ります..."
            )

        # ---------------------------------------------------------------------
        # 戻る
        # ---------------------------------------------------------------------

        elif action == "0":

            continue

        else:

            print(
                "⚠ 無効な選択です。"
            )


# =============================================================================
# このLevelからのルート検索
# =============================================================================

def search_from_level(
    level_id,
    nodes,
    graph,
    edge_info,
):
    """
    対象Levelを起点に目的Levelへのルートを検索する。
    """

    print()
    print("=" * 80)
    print(
        f"【 このLevelからの経路 】 {level_id}"
    )
    print("=" * 80)

    goal_input = input(
        "▶到着Levelを入力 (空欄で戻る) : "
    ).strip()

    if not goal_input:
        return

    goal = find_node(
        nodes,
        goal_input
    )

    if not goal:

        print(
            f"❌ 到着Levelが見つかりません: "
            f"{goal_input}"
        )

        input(
            "\nEnterキーで戻ります..."
        )

        return

    depth_input = input(
        "▶最大経路長 (Enter = 20) : "
    ).strip()

    if depth_input:

        try:

            max_depth = int(
                depth_input
            )

        except ValueError:

            print(
                "整数を入力してください。"
            )

            input(
                "\nEnterキーで戻ります..."
            )

            return

        if max_depth < 1:

            print(
                "最大経路長は1以上にしてください。"
            )

            input(
                "\nEnterキーで戻ります..."
            )

            return

    else:

        max_depth = 20

    result_input = input(
        "▶表示するルート数 (Enter = 10) : "
    ).strip()

    if result_input:

        try:

            max_results = int(
                result_input
            )

        except ValueError:

            print(
                "整数を入力してください。"
            )

            input(
                "\nEnterキーで戻ります..."
            )

            return

        if max_results < 1:

            print(
                "表示件数は1以上にしてください。"
            )

            input(
                "\nEnterキーで戻ります..."
            )

            return

    else:

        max_results = 10

    shortest = find_shortest_distance(
        graph,
        level_id,
        goal
    )

    if shortest is None:

        print()
        print(
            "❌ 指定されたLevel間に"
            "到達可能なルートがありません。"
        )

        input(
            "\nEnterキーで戻ります..."
        )

        return

    routes = find_shortest_routes(
        graph,
        level_id,
        goal,
        max_depth,
        max_results
    )

    if not routes:

        print()
        print(
            "❌ 指定された最大経路長では"
            "ルートが見つかりませんでした。"
        )

        print(
            f"最短距離 : "
            f"{shortest} エッジ"
        )

        input(
            "\nEnterキーで戻ります..."
        )

        return

    print()
    print(
        f"最短距離 : "
        f"{shortest} エッジ"
    )

    print(
        f"表示対象 : "
        f"{len(routes)} 件"
    )

    print(
        f"最大経路長 : "
        f"{max_depth} エッジ"
    )

    for index, route in enumerate(
        routes,
        1
    ):

        print_route(
            index,
            route,
            nodes,
            edge_info
        )

    input(
        "\nEnterキーでLevel Browserに戻ります..."
    )


# =============================================================================
# Level検索
# =============================================================================

def search_level(nodes):
    """
    Levelを検索し、Level IDを返す。

    見つからなければNone。
    """

    while True:

        print()

        query = input(
            "▶Levelを入力 (空欄で終了) : "
        ).strip()

        if not query:

            return None

        candidates = find_level_candidates(
            query
        )

        if not candidates:

            print()
            print(
                f"❌ Levelが見つかりません: "
                f"{query}"
            )

            continue

        # ---------------------------------------------------------------------
        # 1件
        # ---------------------------------------------------------------------

        if len(candidates) == 1:

            return candidates[0]

        # ---------------------------------------------------------------------
        # 複数候補
        # ---------------------------------------------------------------------

        print()
        print(
            "候補が複数あります。"
        )

        for index, candidate in enumerate(
            candidates[:30],
            1
        ):

            info = nodes.get(
                candidate,
                {}
            )

            title = info.get(
                "title",
                ""
            )

            if title:

                print(
                    f'  [{index}] '
                    f'{candidate} "{title}"'
                )

            else:

                print(
                    f"  [{index}] {candidate}"
                )

        if len(candidates) > 30:

            print(
                f"  ... 他 "
                f"{len(candidates) - 30} 件"
            )

        while True:

            choice = input(
                "番号を入力してください"
                "（Enterでキャンセル）: "
            ).strip()

            if not choice:

                return None

            if not choice.isdigit():

                print(
                    "数字を入力してください。"
                )

                continue

            index = int(
                choice
            ) - 1

            if 0 <= index < min(
                len(candidates),
                30
            ):

                return candidates[
                    index
                ]

            print(
                "範囲外の番号です。"
            )


# =============================================================================
# ホーム画面
# =============================================================================

def show_home():
    """
    Level Browserのホーム画面。

    True  : 別Levelを検索
    False : Browser終了
    """

    print()
    print("=" * 80)
    print(
        "        Backrooms Fandom JP - Level Browser"
    )
    print("=" * 80)

    print()
    print(
        "ローカルLevelデータベースを検索します。"
    )

    print()

    level_id = search_level(
        CURRENT_NODES
    )

    if level_id is None:

        return None

    return level_id


# =============================================================================
# Level Browser
# =============================================================================

def browse_level(
    initial_level=None,
    nodes=None,
    graph=None,
    edge_info=None,
):
    """
    Level Browser本体。

    ブラウザー履歴:

        history_back
        history_forward

    を使って、

        [B] 戻る
        [F] 進む
        [H] ホーム

    を実現する。
    """

    global CURRENT_NODES

    # =========================================================================
    # ネットワークデータ読み込み
    # =========================================================================

    if (
        nodes is None
        or graph is None
        or edge_info is None
    ):

        try:

            nodes = load_nodes()

            graph, edge_info = load_edges()

        except Exception as e:

            print()
            print(
                "❌ Levelネットワークデータの読み込みに失敗しました。"
            )

            print(
                f"{type(e).__name__}: {e}"
            )

            return

    CURRENT_NODES = nodes

    # =========================================================================
    # 最初のLevel
    # =========================================================================

    if initial_level is None:

        print()
        print("=" * 80)
        print(
            "        Backrooms Fandom JP - Level Browser"
        )
        print("=" * 80)

        current_level = search_level(
            nodes
        )

        if current_level is None:

            print()
            print(
                "Level Browserを終了します。"
            )

            return

    else:

        current_level = initial_level

    # =========================================================================
    # 履歴
    # =========================================================================

    history_back = []

    history_forward = []

    # =========================================================================
    # Browserメインループ
    # =========================================================================

    while True:

        info = get_level_info(
            current_level
        )

        images = find_level_images(
            current_level
        )

        # ---------------------------------------------------------------------
        # Levelページ
        # ---------------------------------------------------------------------

        print()
        print("=" * 80)

        if info["title"]:

            print(
                f'{info["level_id"]} '
                f'"{info["title"]}"'
            )

        else:

            print(
                info["level_id"]
            )

        print("=" * 80)

        if info["danger"]:

            print(
                f"危険度     : "
                f"{info['danger']}"
            )

        if info["comprehension"]:

            print(
                f"理解度     : "
                f"{info['comprehension']}"
            )

        print(
            f"本文       : "
            f"{'あり' if info['text_file'] else 'なし'}"
        )

        print(
            f"画像       : "
            f"{len(images)} 枚"
        )

        # ---------------------------------------------------------------------
        # 履歴情報
        # ---------------------------------------------------------------------

        if history_back:

            print(
                f"戻る履歴   : "
                f"{len(history_back)} 件"
            )

        if history_forward:

            print(
                f"進む履歴   : "
                f"{len(history_forward)} 件"
            )

        # ---------------------------------------------------------------------
        # メニュー
        # ---------------------------------------------------------------------

        print()

        print(
            "[1] 本文を読む"
        )

        print(
            "[2] 画像を見る"
        )

        print(
            "[3] 接続Levelを見る"
        )

        print(
            "[4] このLevelからの経路"
        )

        print(
            "[5] Levelを再検索"
        )

        if history_back:

            print(
                "[B] 前のLevelへ戻る"
            )

        if history_forward:

            print(
                "[F] 次のLevelへ進む"
            )

        print(
            "[H] ホーム"
        )

        print(
            "[0] Browserを終了"
        )

        print()

        choice = input(
            "▶選択 : "
        ).strip().lower()

        # =====================================================================
        # 1. 本文
        # =====================================================================

        if choice == "1":

            display_text_database(
                info["text_file"]
            )

        # =====================================================================
        # 2. 画像
        # =====================================================================

        elif choice == "2":

            show_images(
                current_level
            )

        # =====================================================================
        # 3. 接続Level
        # =====================================================================

        elif choice == "3":

            target = show_connections(
                current_level,
                graph,
                edge_info,
                nodes
            )

            # -------------------------------------------------------------
            # 接続先へ移動
            # -------------------------------------------------------------

            if target:

                # 現在位置を「戻る」履歴へ
                history_back.append(
                    current_level
                )

                # 新しいページへ移動したため
                # 「進む」履歴は消去
                history_forward.clear()

                current_level = target

        # =====================================================================
        # 4. このLevelからの経路
        # =====================================================================

        elif choice == "4":

            search_from_level(
                current_level,
                nodes,
                graph,
                edge_info
            )

        # =====================================================================
        # 5. Level再検索
        # =====================================================================

        elif choice == "5":

            new_level = search_level(
                nodes
            )

            if new_level is not None:

                history_back.append(
                    current_level
                )

                history_forward.clear()

                current_level = new_level

        # =====================================================================
        # B. 戻る
        # =====================================================================

        elif choice == "b":

            if history_back:

                # 現在位置を進む履歴へ
                history_forward.append(
                    current_level
                )

                # 戻る
                current_level = (
                    history_back.pop()
                )

            else:

                print()
                print(
                    "戻れる履歴がありません。"
                )

        # =====================================================================
        # F. 進む
        # =====================================================================

        elif choice == "f":

            if history_forward:

                # 現在位置を戻る履歴へ
                history_back.append(
                    current_level
                )

                # 進む
                current_level = (
                    history_forward.pop()
                )

            else:

                print()
                print(
                    "進める履歴がありません。"
                )

        # =====================================================================
        # H. ホーム
        # =====================================================================

        elif choice == "h":

            print()
            print("=" * 80)
            print(
                "【 ホーム 】"
            )
            print("=" * 80)

            new_level = search_level(
                nodes
            )

            if new_level is not None:

                # ホームから別Levelへ移動
                history_back.append(
                    current_level
                )

                # 新しいナビゲーションなので
                # 進む履歴はクリア
                history_forward.clear()

                current_level = new_level

        # =====================================================================
        # 0. 終了
        # =====================================================================

        elif choice == "0":

            print()
            print(
                "Level Browserを終了します。"
            )

            return

        # =====================================================================
        # 無効な入力
        # =====================================================================

        else:

            print()
            print(
                "⚠ 無効な選択です。"
            )