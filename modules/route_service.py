"""Non-interactive route graph queries shared by the GUI and AI assistant."""

import csv
import re
from collections import defaultdict, deque
from pathlib import Path

from modules.runtime_paths import get_application_dir


BASE_DIR = get_application_dir()


def _clean(value):
    return str(value or "").strip().strip('"\'”’“‘')


def load_route_graph(base_dir=None):
    """Load route nodes and directed edges from the generated CSV files."""
    root = Path(base_dir) if base_dir else BASE_DIR
    nodes = {}
    edges = defaultdict(list)
    edge_info = defaultdict(list)
    for path in (root / "backrooms_nodes.csv", root / "extracted_fandom_levels_summary.csv"):
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                node_id = _clean(row.get("Id") or row.get("階層コード"))
                if not node_id:
                    continue
                item = nodes.setdefault(node_id, {"title": "", "danger": "", "comprehension": ""})
                for field, keys in {
                    "title": ("Label", "メタタイトル"),
                    "danger": ("Danger", "危険度"),
                    "comprehension": ("Comprehension", "理解度"),
                }.items():
                    value = next((_clean(row.get(key)) for key in keys if _clean(row.get(key))), "")
                    if value:
                        item[field] = value
    wiki_list = root / "wiki_list.txt"
    if wiki_list.is_file():
        with wiki_list.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                if ":" not in line:
                    continue
                node_id, title = (_clean(part) for part in line.split(":", 1))
                if node_id and title:
                    item = nodes.setdefault(node_id, {"title": "", "danger": "", "comprehension": ""})
                    if not item["title"]:
                        item["title"] = title
    edge_file = root / "backrooms_edges.csv"
    if edge_file.is_file():
        with edge_file.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                source, target = _clean(row.get("Source")), _clean(row.get("Target"))
                if not source or not target:
                    continue
                if target not in edges[source]:
                    edges[source].append(target)
                edge_info[(source, target)].append({
                    "type": _clean(row.get("Type")),
                    "category": _clean(row.get("Category")),
                    "condition": _clean(row.get("Condition")),
                })
                nodes.setdefault(source, {"title": "", "danger": "", "comprehension": ""})
                nodes.setdefault(target, {"title": "", "danger": "", "comprehension": ""})
    return nodes, dict(edges), dict(edge_info)


def _resolve(query, nodes):
    q = re.sub(r"\s+", " ", str(query or "").strip()).casefold()
    if not q:
        return []
    exact_ids = [key for key in nodes if key.casefold() == q]
    if exact_ids:
        return exact_ids
    exact_titles = [key for key, value in nodes.items() if value.get("title", "").casefold() == q]
    if exact_titles:
        return exact_titles
    if q.isdigit():
        level = f"level {q}"
        normal = [key for key in nodes if key.casefold() == level]
        if normal:
            return normal
    return [key for key, value in nodes.items() if q in key.casefold() or q in value.get("title", "").casefold()]


def resolve_node_candidates(query, nodes):
    """Public deterministic matcher used by non-interactive and CLI callers."""
    return _resolve(query, nodes)


def _node(node_id, nodes):
    return {"id": node_id, **nodes.get(node_id, {})}


def search_routes(start_query, goal_query, *, base_dir=None, max_results=8, max_depth=30):
    try:
        nodes, graph, edge_info = load_route_graph(base_dir)
    except (OSError, csv.Error, UnicodeError) as error:
        return {"error": f"経路CSVを読み込めません: {error}", "start_candidates": [], "goal_candidates": [], "routes": []}
    if not graph:
        return {"error": "経路CSVが見つからないか、接続データが空です。環境設定タブで接続CSVを生成してください。", "start_candidates": [], "goal_candidates": [], "routes": []}
    starts, goals = _resolve(start_query, nodes), _resolve(goal_query, nodes)
    result = {"start_candidates": [_node(key, nodes) for key in starts[:30]], "goal_candidates": [_node(key, nodes) for key in goals[:30]], "routes": [], "edge_info": {}}
    if len(starts) != 1 or len(goals) != 1:
        result["error"] = "出発地または目的地を一意に特定できません。候補から選ぶか、Level名を詳しく指定してください。"
        return result
    start, goal = starts[0], goals[0]
    result.update({"start": _node(start, nodes), "goal": _node(goal, nodes), "error": ""})
    distances = {start: 0}
    pending = deque([start])
    while pending:
        current = pending.popleft()
        for nxt in graph.get(current, []):
            if nxt not in distances:
                distances[nxt] = distances[current] + 1
                pending.append(nxt)
    if goal not in distances or distances[goal] > max_depth:
        result["message"] = "指定した条件で到達できる経路が見つかりませんでした。"
        return result
    shortest = distances[goal]
    queue, routes = deque([[start]]), []
    while queue and len(routes) < max(1, min(int(max_results), 30)):
        path = queue.popleft()
        if path[-1] == goal:
            routes.append(path)
            continue
        if len(path) - 1 >= shortest:
            continue
        for nxt in graph.get(path[-1], []):
            if nxt not in path and distances.get(nxt) == len(path):
                queue.append(path + [nxt])
    result["routes"] = [[_node(key, nodes) for key in path] for path in routes]
    result["edge_info"] = {f"{source}\n{target}": infos for (source, target), infos in edge_info.items() if any(source in route and target in route for route in routes)}
    result["message"] = f"{len(routes)}件の経路が見つかりました。" if routes else "指定した条件で到達できる経路が見つかりませんでした。"
    return result


def build_route_context(query, *, base_dir=None):
    """Return route evidence only when a query mentions a route or location."""
    text = str(query or "")
    if not re.search(
        r"経路|ルート|出口|脱出|移動|行き方|到達|どこに|放浪者|トラベラー|"
        r"特異点|召喚|瞬間移動|テレポート|移動条件|Level\s*[-0-9A-Za-z.]+",
        text,
        re.I,
    ):
        return ""
    try:
        nodes, graph, edge_info = load_route_graph(base_dir)
    except (OSError, csv.Error, UnicodeError) as error:
        return f"経路CSVを読み込めません: {error}"
    if not graph:
        return "経路ネットワークCSVは未生成です。経路の接続情報を確認できません。"
    mentions = []
    pattern = re.compile(r"(?:Level\s*[-0-9A-Za-z.]+(?:\s*η)?|The\s+[A-Za-z][A-Za-z ]+)", re.I)
    for match in pattern.finditer(text):
        mentions.extend(_resolve(match.group(0), nodes))
    mentions = list(dict.fromkeys(mentions))
    normalized_query = re.sub(r"\s+", "", _clean(text).casefold())
    condition_matches = []
    for (source, target), infos in edge_info.items():
        if mentions and not ({source, target} & set(mentions)):
            continue
        for info in infos:
            condition = _clean(info.get("condition"))
            normalized_condition = re.sub(r"\s+", "", condition.casefold())
            if len(normalized_condition) >= 4 and normalized_condition in normalized_query:
                condition_matches.append((source, target, info))
    if condition_matches:
        rows = ["ローカル経路グラフ（質問に含まれる接続条件と一致。CSV記載に限る）:"]
        for source, target, info in condition_matches[:12]:
            rows.append(f"- {source} → {target}")
            detail = ", ".join(f"{key}={value}" for key, value in info.items() if value)
            if detail:
                rows.append(f"  接続情報: {detail}")
        return "\n".join(rows)
    if len(mentions) == 1:
        current = mentions[0]
        choices = graph.get(current, [])
        if not choices:
            return f"{current} から出る登録済み接続は見つかりませんでした。接続CSVは網羅的とは限りません。"
        rows = [f"ローカル経路グラフ（{current} からの直接接続候補。現実の安全保証ではありません）:"]
        rows.append(f"現在地: {_node(current, nodes)}")
        for target in choices[:12]:
            item = _node(target, nodes)
            rows.append(f"- {target} ({item.get('title') or '題名なし'}; 危険度={item.get('danger') or '未登録'}; 理解度={item.get('comprehension') or '未登録'})")
            for info in edge_info.get((current, target), []):
                note = ", ".join(f"{key}={value}" for key, value in info.items() if value)
                if note:
                    rows.append(f"  接続情報: {note}")
        return "\n".join(rows)
    if len(mentions) < 2:
        return "経路データは利用可能ですが、質問から対象のLevelを特定できませんでした。"
    result = search_routes(mentions[0], mentions[1], base_dir=base_dir, max_results=3)
    if result.get("error"):
        return f"ローカル経路データ: {result['error']}"
    rows = ["ローカル経路グラフ（有向接続CSV、実世界の安全保証ではありません）:"]
    for route in result["routes"]:
        rows.append(" → ".join(f"{node['id']} ({node.get('title') or '題名なし'}; 危険度={node.get('danger') or '未登録'}; 理解度={node.get('comprehension') or '未登録'})" for node in route))
        for a, b in zip(route, route[1:]):
            infos = edge_info.get((a["id"], b["id"]), [])
            for info in infos:
                note = ", ".join(f"{k}={v}" for k, v in info.items() if v)
                if note:
                    rows.append(f"  {a['id']} → {b['id']}: {note}")
    return "\n".join(rows)
