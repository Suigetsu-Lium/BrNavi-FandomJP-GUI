"""
Backrooms Navigation System - Fandom JP
Route Navigation Module

backrooms_nodes.csv
backrooms_edges.csv
extracted_fandom_levels_summary.csv
wiki_list.txt

を読み込み、Level間の経路を検索する。
"""

import csv
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path


# =============================================================================
# 基本パス
# =============================================================================

# modules/ の1つ上 = BrNavi-FandomJP
BASE_DIR = Path(__file__).resolve().parent.parent

NODES_FILE = BASE_DIR / "backrooms_nodes.csv"
EDGES_FILE = BASE_DIR / "backrooms_edges.csv"
SUMMARY_FILE = BASE_DIR / "extracted_fandom_levels_summary.csv"
WIKI_LIST_FILE = BASE_DIR / "wiki_list.txt"

# 検索結果保存フォルダ
RESULT_DIR = BASE_DIR / "ルート検索結果"


# =============================================================================
# テキスト整形
# =============================================================================

def clean_meta_value(val):
    """
    余分な引用符や空白、無効な文字を取り除く。
    """

    if not val:
        return ""

    val = val.strip().strip(
        '"\'”’“‘'
    )

    if val in (
        r"],]",
        r"]",
        r"",
    ):
        return ""

    return val.strip()


# =============================================================================
# ノード読み込み
# =============================================================================

def load_nodes():
    """
    backrooms_nodes.csv を基本とし、

    - extracted_fandom_levels_summary.csv
    - wiki_list.txt

    からメタタイトル、危険度、理解度を補完して読み込む。
    """

    nodes = {}

    # -------------------------------------------------------------------------
    # 1. backrooms_nodes.csv
    # -------------------------------------------------------------------------

    if NODES_FILE.exists():

        with open(
            NODES_FILE,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                node_id = row.get(
                    "Id",
                    ""
                ).strip()

                if not node_id:
                    continue

                label = row.get(
                    "Label",
                    ""
                ).strip()

                title = (
                    label
                    if label and label != node_id
                    else ""
                )

                nodes[node_id] = {
                    "title": clean_meta_value(title),
                    "comprehension": clean_meta_value(
                        row.get(
                            "Comprehension",
                            ""
                        )
                    ),
                    "danger": clean_meta_value(
                        row.get(
                            "Danger",
                            ""
                        )
                    ),
                }

    # -------------------------------------------------------------------------
    # 2. extracted_fandom_levels_summary.csv から補完
    # -------------------------------------------------------------------------

    if SUMMARY_FILE.exists():

        with open(
            SUMMARY_FILE,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as file:

            reader = csv.DictReader(file)

            for row in reader:

                node_id = row.get(
                    "階層コード",
                    ""
                ).strip()

                if not node_id:
                    continue

                title = clean_meta_value(
                    row.get(
                        "メタタイトル",
                        ""
                    )
                )

                danger = clean_meta_value(
                    row.get(
                        "危険度",
                        ""
                    )
                )

                comprehension = clean_meta_value(
                    row.get(
                        "理解度",
                        ""
                    )
                )

                if node_id not in nodes:

                    nodes[node_id] = {
                        "title": "",
                        "comprehension": "",
                        "danger": "",
                    }

                if title:
                    nodes[node_id]["title"] = title

                if danger:
                    nodes[node_id]["danger"] = danger

                if comprehension:
                    nodes[node_id]["comprehension"] = (
                        comprehension
                    )

    # -------------------------------------------------------------------------
    # 3. wiki_list.txt からメタタイトル補完
    # -------------------------------------------------------------------------

    if WIKI_LIST_FILE.exists():

        with open(
            WIKI_LIST_FILE,
            "r",
            encoding="utf-8-sig"
        ) as file:

            for line in file:

                line = line.strip()

                if not line or ":" not in line:
                    continue

                parts = line.split(
                    ":",
                    1
                )

                node_id = parts[0].strip()

                title = clean_meta_value(
                    parts[1]
                )

                if node_id not in nodes:

                    nodes[node_id] = {
                        "title": "",
                        "comprehension": "",
                        "danger": "",
                    }

                if (
                    title
                    and not nodes[node_id]["title"]
                ):

                    nodes[node_id]["title"] = title

    return nodes


# =============================================================================
# エッジ読み込み
# =============================================================================

def load_edges():
    """
    backrooms_edges.csv を読み込む。
    """

    graph = defaultdict(list)
    edge_info = defaultdict(list)

    if not EDGES_FILE.exists():
        raise FileNotFoundError(
            f"edges CSV が見つかりません:\n{EDGES_FILE}"
        )

    with open(
        EDGES_FILE,
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as file:

        reader = csv.DictReader(file)

        for row in reader:

            source = row.get(
                "Source",
                ""
            ).strip()

            target = row.get(
                "Target",
                ""
            ).strip()

            if not source or not target:
                continue

            if target not in graph[source]:
                graph[source].append(
                    target
                )

            edge_info[(source, target)].append({
                "type": row.get(
                    "Type",
                    ""
                ).strip(),

                "category": row.get(
                    "Category",
                    ""
                ).strip(),

                "condition": row.get(
                    "Condition",
                    ""
                ).strip(),
            })

    return graph, edge_info


# =============================================================================
# Level検索
# =============================================================================

def find_node(nodes, query):
    """
    入力文字列からLevel IDを検索する。

    優先順位:

    1. 完全一致
    2. 数字だけの入力なら通常の「Level X」
    3. 通常の「Level X」が存在しない場合のみ「Level X η」
    4. 大文字小文字を無視した完全一致
    5. 部分一致
    """

    query = query.strip()

    if not query:
        return None

    # =========================================================================
    # 1. 完全一致
    #
    # 例:
    #   Level 1   → Level 1
    #   Level 1 η → Level 1 η
    # =========================================================================

    if query in nodes:
        return query

    # =========================================================================
    # 2. 数字だけの入力
    #
    # 例:
    #   1 → Level 1
    #
    # ただし Level 1 が存在せず Level 1 η しかない場合は、
    #   1 → Level 1 η
    #
    # とする。
    # =========================================================================

    if query.lstrip("-").isdigit():

        # 通常Levelを優先
        normal_candidate = f"Level {query}"

        if normal_candidate in nodes:
            return normal_candidate

        # 通常Levelが存在しない場合のみη階層
        eta_candidate = f"Level {query} η"

        if eta_candidate in nodes:
            return eta_candidate

    # =========================================================================
    # 3. 大文字小文字を無視した完全一致
    # =========================================================================

    query_lower = query.lower()

    exact_matches = [
        node_id
        for node_id in nodes
        if node_id.lower() == query_lower
    ]

    if len(exact_matches) == 1:
        return exact_matches[0]

    # =========================================================================
    # 4. 部分一致
    # =========================================================================

    partial_matches = [
        node_id
        for node_id in nodes
        if query_lower in node_id.lower()
    ]

    if len(partial_matches) == 0:
        return None

    if len(partial_matches) == 1:
        return partial_matches[0]

    # =========================================================================
    # 5. 複数候補
    # =========================================================================

    print()
    print("候補が複数あります。")

    for index, node_id in enumerate(
        partial_matches[:30],
        1
    ):

        info = nodes.get(
            node_id,
            {}
        )

        title = info.get(
            "title",
            ""
        )

        title_str = (
            f' - "{title}"'
            if title
            else ""
        )

        print(
            f"  [{index}] {node_id}{title_str}"
        )

    if len(partial_matches) > 30:

        print(
            f"  ... 他 "
            f"{len(partial_matches) - 30} 件"
        )

    while True:

        choice = input(
            "番号を入力してください（Enterでキャンセル）: "
        ).strip()

        if not choice:
            return None

        if not choice.isdigit():

            print(
                "数字を入力してください。"
            )

            continue

        index = int(choice) - 1

        if 0 <= index < len(partial_matches):
            return partial_matches[index]

        print(
            "範囲外の番号です。"
        )


# =============================================================================
# 最短距離
# =============================================================================

def find_shortest_distance(
    graph,
    start,
    goal
):
    """
    BFSで最短距離を求める。
    """

    queue = deque([
        (start, 0)
    ])

    visited = {
        start
    }

    while queue:

        current, distance = queue.popleft()

        if current == goal:
            return distance

        for next_node in graph.get(
            current,
            []
        ):

            if next_node in visited:
                continue

            visited.add(
                next_node
            )

            queue.append(
                (
                    next_node,
                    distance + 1
                )
            )

    return None


# =============================================================================
# 経路探索
# =============================================================================

def find_shortest_routes(
    graph,
    start,
    goal,
    max_depth,
    max_results
):
    """
    BFSで単純経路を探索する。
    """

    routes = []

    queue = deque([
        [start]
    ])

    while (
        queue
        and len(routes) < max_results
    ):

        path = queue.popleft()

        current = path[-1]

        depth = len(path) - 1

        # ---------------------------------------------------------------------
        # 到着
        # ---------------------------------------------------------------------

        if current == goal:

            if start != goal:
                routes.append(
                    path
                )

            continue

        # ---------------------------------------------------------------------
        # 深さ制限
        # ---------------------------------------------------------------------

        if depth >= max_depth:
            continue

        # ---------------------------------------------------------------------
        # 次のLevel
        # ---------------------------------------------------------------------

        for next_node in graph.get(
            current,
            []
        ):

            # 同一経路内で同じLevelを再訪しない
            if next_node in path:
                continue

            queue.append(
                path + [next_node]
            )

    return routes


# =============================================================================
# Level表示
# =============================================================================

def format_node_display(
    index,
    node_id,
    nodes
):
    """
    Level表示文字列を生成する。

    例:
        [1] Level 0 η "ロビー" (危険度: 1/5, 理解度: 90%)
    """

    info = nodes.get(
        node_id,
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

    parts = [
        f"[{index}] {node_id}"
    ]

    if title:

        parts.append(
            f'"{title}"'
        )

    meta_details = []

    if danger:

        meta_details.append(
            f"危険度: {danger}"
        )

    if comprehension:

        meta_details.append(
            f"理解度: {comprehension}"
        )

    if meta_details:

        parts.append(
            f"({', '.join(meta_details)})"
        )

    return " ".join(parts)


# =============================================================================
# 接続条件表示
# =============================================================================

def print_edge_info(
    source,
    target,
    edge_info
):
    """
    2つのLevel間の接続条件を表示する。
    """

    infos = edge_info.get(
        (source, target),
        []
    )

    if not infos:
        return

    print(
        f"    → {target}"
    )

    if len(infos) == 1:

        info = infos[0]

        if info["type"]:

            print(
                f"      Type     : "
                f"{info['type']}"
            )

        if info["category"]:

            print(
                f"      Category : "
                f"{info['category']}"
            )

        if info["condition"]:

            print(
                f"      条件     : "
                f"{info['condition']}"
            )

    else:

        print(
            f"      接続条件 : "
            f"{len(infos)} 件"
        )

        for index, info in enumerate(
            infos,
            1
        ):

            print(
                f"      [{index}]"
            )

            if info["type"]:

                print(
                    f"        Type     : "
                    f"{info['type']}"
                )

            if info["category"]:

                print(
                    f"        Category : "
                    f"{info['category']}"
                )

            if info["condition"]:

                print(
                    f"        条件     : "
                    f"{info['condition']}"
                )


# =============================================================================
# ルート表示
# =============================================================================

def print_route(
    route_number,
    route,
    nodes,
    edge_info
):
    """
    検索結果のルートを画面に表示する。
    """

    distance = len(route) - 1

    print()
    print("=" * 80)
    print(
        f"【ルート {route_number}】 "
        f"{distance} エッジ"
    )
    print("=" * 80)

    for index, node_id in enumerate(
        route,
        1
    ):

        print()

        print(
            format_node_display(
                index,
                node_id,
                nodes
            )
        )

        if index < len(route):

            next_node = route[index]

            print()

            print_edge_info(
                node_id,
                next_node,
                edge_info
            )


# =============================================================================
# ファイル名整形
# =============================================================================

def sanitize_filename(name):
    """
    Windowsのファイル名として使用できない文字を置換する。
    """

    invalid_chars = '<>:"/\\|?*'

    for char in invalid_chars:

        name = name.replace(
            char,
            "_"
        )

    return name.rstrip(
        " ."
    )


# =============================================================================
# 検索結果保存
# =============================================================================

def save_search_result(
    search_datetime,
    start_input,
    goal_input,
    start,
    goal,
    max_depth,
    max_results,
    nodes,
    graph,
    shortest_distance,
    routes,
    edge_info,
):
    """
    検索結果をテキストファイルとして保存する。
    """

    RESULT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    timestamp = (
        search_datetime.strftime(
            "%Y%m%d%H%M%S"
        )
    )

    filename = (
        f"{sanitize_filename(start)} "
        f"to "
        f"{sanitize_filename(goal)}_"
        f"{timestamp}.txt"
    )

    output_file = (
        RESULT_DIR / filename
    )

    lines = []

    lines.append("=" * 80)
    lines.append(
        "    Backrooms Fandom JP - Level Route Searcher"
    )
    lines.append("=" * 80)

    lines.append("")

    lines.append("【検索日時】")
    lines.append(
        search_datetime.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    lines.append("")

    lines.append("【検索設定】")
    lines.append(
        f"  出発Level入力 : {start_input}"
    )
    lines.append(
        f"  到着Level入力 : {goal_input}"
    )
    lines.append(
        f"  出発Level     : {start}"
    )
    lines.append(
        f"  到着Level     : {goal}"
    )
    lines.append(
        f"  最大経路長    : {max_depth} エッジ"
    )
    lines.append(
        f"  最大表示件数  : {max_results} 件"
    )

    lines.append("")

    lines.append("【データ情報】")
    lines.append(
        f"  ノード数 : {len(nodes):,}"
    )

    edge_count = sum(
        len(targets)
        for targets in graph.values()
    )

    lines.append(
        f"  エッジ数 : {edge_count:,}"
    )

    lines.append("")

    lines.append("【検索結果】")
    lines.append(
        f"  最短距離 : {shortest_distance} エッジ"
    )
    lines.append(
        f"  表示対象 : {len(routes)} 件"
    )
    lines.append(
        f"  最大経路長 : {max_depth} エッジ"
    )

    # -------------------------------------------------------------------------
    # 各ルート
    # -------------------------------------------------------------------------

    for route_number, route in enumerate(
        routes,
        1
    ):

        distance = len(route) - 1

        lines.append("")
        lines.append("=" * 80)
        lines.append(
            f"【ルート {route_number}】 "
            f"{distance} エッジ"
        )
        lines.append("=" * 80)

        for index, node_id in enumerate(
            route,
            1
        ):

            lines.append("")

            lines.append(
                format_node_display(
                    index,
                    node_id,
                    nodes
                )
            )

            if index >= len(route):
                continue

            next_node = route[index]

            lines.append("")

            infos = edge_info.get(
                (node_id, next_node),
                []
            )

            if not infos:
                continue

            lines.append(
                f"    → {next_node}"
            )

            if len(infos) == 1:

                info = infos[0]

                if info["type"]:

                    lines.append(
                        f"      Type     : "
                        f"{info['type']}"
                    )

                if info["category"]:

                    lines.append(
                        f"      Category : "
                        f"{info['category']}"
                    )

                if info["condition"]:

                    lines.append(
                        f"      条件     : "
                        f"{info['condition']}"
                    )

            else:

                lines.append(
                    f"      接続条件 : "
                    f"{len(infos)} 件"
                )

                for info_index, info in enumerate(
                    infos,
                    1
                ):

                    lines.append(
                        f"      [{info_index}]"
                    )

                    if info["type"]:

                        lines.append(
                            f"        Type     : "
                            f"{info['type']}"
                        )

                    if info["category"]:

                        lines.append(
                            f"        Category : "
                            f"{info['category']}"
                        )

                    if info["condition"]:

                        lines.append(
                            f"        条件     : "
                            f"{info['condition']}"
                        )

    lines.append("")
    lines.append("=" * 80)
    lines.append("【検索完了】")
    lines.append("=" * 80)

    with open(
        output_file,
        "w",
        encoding="utf-8-sig",
        newline="\n"
    ) as file:

        file.write(
            "\n".join(lines)
        )

    return output_file


# =============================================================================
# ナビゲーション実行
# =============================================================================

def run_navigation():
    """
    Route Searcher本体。

    BackroomsNavigationSystem_FandomJP.py から呼び出す。
    """

    print()
    print("=" * 80)
    print(
        "    Backrooms Fandom JP - Level Route Searcher"
    )
    print("=" * 80)

    # =========================================================================
    # データファイル確認
    # =========================================================================

    if not NODES_FILE.exists():

        print()
        print("[エラー]")
        print(
            "nodes CSV が見つかりません:"
        )
        print(
            NODES_FILE
        )

        return

    if not EDGES_FILE.exists():

        print()
        print("[エラー]")
        print(
            "edges CSV が見つかりません:"
        )
        print(
            EDGES_FILE
        )

        return

    # =========================================================================
    # データ読み込み
    # =========================================================================

    print()
    print(
        "CSVデータを読み込んでいます..."
    )

    try:

        nodes = load_nodes()

        graph, edge_info = load_edges()

    except Exception as error:

        print()
        print("[エラー]")
        print(
            "データ読み込み中にエラーが発生しました。"
        )
        print(
            error
        )

        return

    edge_count = sum(
        len(targets)
        for targets in graph.values()
    )

    print(
        f"    ノード数 : {len(nodes):,}"
    )

    print(
        f"    エッジ数 : {edge_count:,}"
    )

    # =========================================================================
    # 検索ループ
    # =========================================================================

    while True:

        print()
        print("=" * 80)

        start_input = input(
            "▶出発Levelを入力 (空欄で終了) : "
        ).strip()

        if not start_input:

            print(
                "\n検索を終了します。"
            )

            break

        goal_input = input(
            "▶到着Levelを入力 (空欄で終了) : "
        ).strip()

        if not goal_input:

            print(
                "\n検索を終了します。"
            )

            break

        # ---------------------------------------------------------------------
        # 出発Level検索
        # ---------------------------------------------------------------------

        start = find_node(
            nodes,
            start_input
        )

        if not start:

            print(
                f"\n[エラー] "
                f"出発Levelが見つかりません: "
                f"{start_input}"
            )

            continue

        # ---------------------------------------------------------------------
        # 到着Level検索
        # ---------------------------------------------------------------------

        goal = find_node(
            nodes,
            goal_input
        )

        if not goal:

            print(
                f"\n[エラー] "
                f"到着Levelが見つかりません: "
                f"{goal_input}"
            )

            continue

        # ---------------------------------------------------------------------
        # 出発・到着表示
        # ---------------------------------------------------------------------

        print()

        start_display = (
            format_node_display(
                1,
                start,
                nodes
            )
            .split(
                " ",
                1
            )[1]
        )

        goal_display = (
            format_node_display(
                1,
                goal,
                nodes
            )
            .split(
                " ",
                1
            )[1]
        )

        print(
            f"出発 : {start_display}"
        )

        print(
            f"到着 : {goal_display}"
        )

        # ---------------------------------------------------------------------
        # 最大経路長
        # ---------------------------------------------------------------------

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

                continue

            if max_depth < 1:

                print(
                    "最大経路長は1以上にしてください。"
                )

                continue

        else:

            max_depth = 20

        # ---------------------------------------------------------------------
        # 最大表示件数
        # ---------------------------------------------------------------------

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

                continue

            if max_results < 1:

                print(
                    "表示件数は1以上にしてください。"
                )

                continue

        else:

            max_results = 10

        search_datetime = datetime.now()

        # ---------------------------------------------------------------------
        # 検索
        # ---------------------------------------------------------------------

        print()

        print("=" * 80)

        print(
            "🔍 ルートを検索しています..."
        )

        print(
            "=" * 80
        )

        shortest_distance = find_shortest_distance(
            graph,
            start,
            goal
        )

        if shortest_distance is None:

            print()
            print(
                "❌ 指定されたLevel間に"
                "到達可能なルートがありません。"
            )

            continue

        routes = find_shortest_routes(
            graph,
            start,
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
                f"{shortest_distance} エッジ"
            )

            continue

        # ---------------------------------------------------------------------
        # 結果表示
        # ---------------------------------------------------------------------

        print()

        print(
            f"最短距離 : "
            f"{shortest_distance} エッジ"
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

        print()

        print("=" * 80)
        print("【検索結果】")
        print(
            f"{len(routes)} 件のルートを表示"
        )
        print(
            f"最短距離 : "
            f"{shortest_distance} エッジ"
        )
        print(
            f"最大経路長 : "
            f"{max_depth} エッジ"
        )
        print("=" * 80)

        # ---------------------------------------------------------------------
        # 結果保存
        # ---------------------------------------------------------------------

        print()

        save_choice = input(
            "▶検索結果をテキストファイルに保存しますか？ (Y/N) : "
        ).strip().lower()

        if save_choice in (
            "y",
            "yes"
        ):

            try:

                output_file = save_search_result(
                    search_datetime=search_datetime,
                    start_input=start_input,
                    goal_input=goal_input,
                    start=start,
                    goal=goal,
                    max_depth=max_depth,
                    max_results=max_results,
                    nodes=nodes,
                    graph=graph,
                    shortest_distance=shortest_distance,
                    routes=routes,
                    edge_info=edge_info,
                )

                print()
                print(
                    "✅ 検索結果を保存しました。"
                )

                print(
                    f"保存先 : {output_file}"
                )

            except Exception as error:

                print()
                print(
                    "[エラー] "
                    "検索結果の保存に失敗しました。"
                )

                print(
                    error
                )

        else:

            print()
            print(
                "検索結果は保存せず、"
                "そのまま続行します。"
            )