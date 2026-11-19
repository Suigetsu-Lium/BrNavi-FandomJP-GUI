"""Shared route graph loader and search for legacy and evidence-based CSV schemas."""
import csv
import json
import re
from collections import defaultdict, deque
from pathlib import Path

from modules.runtime_paths import get_application_dir

BASE_DIR = get_application_dir()


def _clean(value):
    return str(value or "").strip().strip('"\'「」『』')


def _blank_node():
    return {"title": "", "danger": "", "comprehension": "", "aliases": [], "url": "", "node_type": "Unknown"}


def _load_v2(root):
    nodes = {}
    graph = defaultdict(list)
    edge_info = defaultdict(list)
    node_file = root / "backrooms_nodes_v2.csv"
    edge_file = root / "backrooms_edges_v2.csv"
    evidence_file = root / "backrooms_evidence_v2.csv"
    if not (node_file.is_file() and edge_file.is_file() and evidence_file.is_file()):
        raise FileNotFoundError("v2 route CSV set is incomplete")

    with node_file.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            node_id = _clean(row.get("Node_ID"))
            if not node_id:
                continue
            try:
                aliases = json.loads(row.get("Aliases") or "[]")
                if not isinstance(aliases, list):
                    aliases = []
            except (TypeError, ValueError):
                aliases = []
            nodes[node_id] = {
                "title": _clean(row.get("Canonical_Title")),
                "danger": _clean(row.get("Danger")),
                "comprehension": _clean(row.get("Comprehension")),
                "aliases": [_clean(value) for value in aliases if _clean(value)],
                "url": _clean(row.get("Canonical_URL")),
                "node_type": _clean(row.get("Node_Type")) or "Unknown",
                "data_status": _clean(row.get("Data_Status")) or "NeedsReview",
                "schema": "v2",
            }

    evidence_by_edge = defaultdict(list)
    if evidence_file.is_file():
        with evidence_file.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                edge_id = _clean(row.get("Edge_ID"))
                if edge_id:
                    evidence_by_edge[edge_id].append({
                        "article_id": _clean(row.get("Article_ID")),
                        "relation_type": _clean(row.get("Relation_Type")),
                        "source_raw": _clean(row.get("Source_Raw")),
                        "target_raw": _clean(row.get("Target_Raw")),
                        "condition": _clean(row.get("Raw_Text")),
                        "extract_method": _clean(row.get("Extract_Method")),
                        "confidence": _clean(row.get("Confidence")),
                        "certainty": _clean(row.get("Certainty")) or "Unknown",
                        "availability": _clean(row.get("Availability")) or "Unknown",
                        "evidence_type": _clean(row.get("Evidence_Type")) or "Unknown",
                        "probability": _clean(row.get("Probability")) or "Unknown",
                    })

    with edge_file.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            source = _clean(row.get("Source_ID"))
            target = _clean(row.get("Target_ID"))
            if source not in nodes or target not in nodes or source == target:
                continue
            direction = _clean(row.get("Direction")) or "Unknown"
            edge = {
                "edge_id": _clean(row.get("Edge_ID")),
                "type": direction,
                "category": "",
                "condition": _clean(row.get("Condition")),
                "certainty": _clean(row.get("Certainty")) or "Unknown",
                "availability": _clean(row.get("Availability")) or "Unknown",
                "evidence_type": _clean(row.get("Evidence_Type")) or "Unknown",
                "probability": _clean(row.get("Probability")) or "Unknown",
                "review_status": _clean(row.get("Review_Status")) or "NeedsReview",
                "evidence": evidence_by_edge.get(_clean(row.get("Edge_ID")), []),
                "schema": "v2",
            }
            if target not in graph[source]:
                graph[source].append(target)
            edge_info[(source, target)].append(edge)
            if direction == "Bidirectional" and source not in graph[target]:
                graph[target].append(source)
                reverse = dict(edge)
                reverse["condition"] = edge["condition"]
                reverse["reversed"] = True
                edge_info[(target, source)].append(reverse)
    return nodes, dict(graph), dict(edge_info)


def _load_legacy(root):
    nodes = {}
    graph = defaultdict(list)
    edge_info = defaultdict(list)
    for path in (root / "backrooms_nodes.csv", root / "extracted_fandom_levels_summary.csv"):
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                node_id = _clean(row.get("Id") or row.get("階層コード"))
                if not node_id:
                    continue
                item = nodes.setdefault(node_id, _blank_node())
                for field, keys in {
                    "title": ("Label", "階層タイトル"),
                    "danger": ("Danger", "危険度"),
                    "comprehension": ("Comprehension", "理解度"),
                }.items():
                    value = next((_clean(row.get(key)) for key in keys if _clean(row.get(key))), "")
                    if value and not item[field]:
                        item[field] = value
    wiki_list = root / "wiki_list.txt"
    if wiki_list.is_file():
        with wiki_list.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                if ":" not in line:
                    continue
                node_id, title = (_clean(part) for part in line.split(":", 1))
                if node_id and title:
                    item = nodes.setdefault(node_id, _blank_node())
                    if not item["title"]:
                        item["title"] = title
    edge_file = root / "backrooms_edges.csv"
    if edge_file.is_file():
        with edge_file.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                source, target = _clean(row.get("Source")), _clean(row.get("Target"))
                if not source or not target or source == target:
                    continue
                if target not in graph[source]:
                    graph[source].append(target)
                edge_info[(source, target)].append({
                    "type": _clean(row.get("Type")),
                    "category": _clean(row.get("Category")),
                    "condition": _clean(row.get("Condition")),
                    "certainty": _clean(row.get("Certainty")) or "Unknown",
                    "availability": "Unknown",
                    "evidence_type": "Unknown",
                    "probability": "Unknown",
                    "review_status": "NeedsReview",
                    "source_kind": _clean(row.get("SourceKind")) or "Unknown",
                    "target_kind": _clean(row.get("TargetKind")) or "Unknown",
                    "evidence": _clean(row.get("Evidence")) or "Legacy",
                    "schema": "legacy",
                })
                nodes.setdefault(source, _blank_node())
                nodes.setdefault(target, _blank_node())
    return nodes, dict(graph), dict(edge_info)


def load_route_graph(base_dir=None):
    """Prefer a complete v2 CSV set; otherwise load the legacy CSV schema."""
    root = Path(base_dir) if base_dir else BASE_DIR
    if (root / "backrooms_nodes_v2.csv").is_file():
        return _load_v2(root)
    return _load_legacy(root)


def _resolve(query, nodes):
    q = re.sub(r"\s+", " ", str(query or "").strip()).casefold()
    if not q:
        return []
    def labels(key, item):
        return [key, item.get("title", ""), *item.get("aliases", [])]
    exact_ids = [key for key in nodes if key.casefold() == q]
    if exact_ids:
        return exact_ids
    exact_labels = [key for key, value in nodes.items() if any(label.casefold() == q for label in labels(key, value) if label)]
    if exact_labels:
        return exact_labels
    if q.isdigit():
        level = f"level {q}"
        normal = [key for key, value in nodes.items() if any(label.casefold() == level for label in labels(key, value) if label)]
        if normal:
            return normal
    return [key for key, value in nodes.items() if any(q in label.casefold() for label in labels(key, value) if label)]


def resolve_node_candidates(query, nodes):
    return _resolve(query, nodes)


def _node(node_id, nodes):
    item = nodes.get(node_id, {})
    aliases = item.get("aliases", [])
    display_id = (aliases[0] if aliases else item.get("title") or node_id) if item.get("schema") == "v2" else node_id
    return {
        "id": display_id,
        "node_id": node_id,
        **item,
    }


def _edge_allowed(info, *, include_uncertain=False, include_unknown=False,
                  include_rumor=False, include_closed=False, include_needs_review=False):
    if info.get("schema") == "legacy":
        return info.get("evidence_type") not in {"False", "Meta"}
    certainty = info.get("certainty", "Unknown")
    availability = info.get("availability", "Unknown")
    evidence_type = info.get("evidence_type", "Unknown")
    review = info.get("review_status", "NeedsReview")
    if evidence_type in {"False", "Meta"}:
        return False
    if evidence_type == "Rumor" and not include_rumor:
        return False
    if certainty == "Uncertain" and not include_uncertain:
        return False
    if certainty == "Unknown" and not include_unknown:
        return False
    if availability in {"Closed", "Disappeared"} and not include_closed:
        return False
    if availability == "Unknown" and not include_unknown:
        return False
    if availability == "Available" and certainty == "Confirmed" and review == "Reviewed":
        return True
    if review != "Reviewed" and not include_needs_review:
        return False
    return True


def search_routes(start_query, goal_query, *, base_dir=None, max_results=8, max_depth=30,
                  include_uncertain=False, include_unknown=False, include_rumor=False,
                  include_closed=False, include_needs_review=False):
    try:
        nodes, _all_graph, all_edge_info = load_route_graph(base_dir)
    except (OSError, csv.Error, UnicodeError, ValueError) as error:
        return {"error": f"ルートCSVを読み込めません: {error}", "start_candidates": [], "goal_candidates": [], "routes": []}
    if not all_edge_info:
        return {"error": "ルートCSVが見つからないか、接続データが空です。", "start_candidates": [], "goal_candidates": [], "routes": []}

    starts, goals = _resolve(start_query, nodes), _resolve(goal_query, nodes)
    result = {
        "start_candidates": [_node(key, nodes) for key in starts[:30]],
        "goal_candidates": [_node(key, nodes) for key in goals[:30]],
        "routes": [], "edge_info": {},
    }
    if len(starts) != 1 or len(goals) != 1:
        result["error"] = "出発地または目的地を一意に特定できません。候補から選ぶか、Level名を詳しく指定してください。"
        return result
    start, goal = starts[0], goals[0]
    result.update({"start": _node(start, nodes), "goal": _node(goal, nodes), "error": ""})

    graph = defaultdict(list)
    selected_info = {}
    for pair, infos in all_edge_info.items():
        accepted = [info for info in infos if _edge_allowed(
            info, include_uncertain=include_uncertain, include_unknown=include_unknown,
            include_rumor=include_rumor, include_closed=include_closed,
            include_needs_review=include_needs_review,
        )]
        if accepted:
            graph[pair[0]].append(pair[1])
            selected_info[pair] = accepted

    distances = {start: 0}
    pending = deque([start])
    while pending:
        current = pending.popleft()
        for nxt in graph.get(current, []):
            if nxt not in distances:
                distances[nxt] = distances[current] + 1
                pending.append(nxt)
    if goal not in distances or distances[goal] > max_depth:
        result["message"] = "指定した条件では到達可能な経路が見つかりませんでした。"
        result["available_edge_count"] = sum(len(v) for v in selected_info.values())
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
    route_pairs = {(a, b) for path in routes for a, b in zip(path, path[1:])}
    result["edge_info"] = {
        f"{_node(source, nodes)['id']}\n{_node(target, nodes)['id']}": infos
        for (source, target), infos in selected_info.items() if (source, target) in route_pairs
    }
    result["message"] = f"{len(routes)}件の経路が見つかりました。" if routes else "条件に一致する経路が見つかりませんでした。"
    result["available_edge_count"] = sum(len(v) for v in selected_info.values())
    result["filters"] = {
        "include_uncertain": include_uncertain, "include_unknown": include_unknown,
        "include_rumor": include_rumor, "include_closed": include_closed,
        "include_needs_review": include_needs_review,
    }
    return result


def build_route_context(query, *, base_dir=None):
    text = str(query or "")
    if not re.search(r"経路|ルート|出口|入口|移動|行き来|到達|どこ|旅行|Level\s*[-0-9A-Za-z.]+", text, re.I):
        return ""
    try:
        nodes, graph, edge_info = load_route_graph(base_dir)
    except (OSError, csv.Error, UnicodeError, ValueError) as error:
        return f"ルートCSVを読み込めません: {error}"
    if not graph:
        return "ローカルルートネットワークCSVは未設定です。"
    mentions = []
    pattern = re.compile(r"(?:Level\s*[-0-9A-Za-z.]+(?:\s*η)?|The\s+[A-Za-z][A-Za-z ]+)", re.I)
    for match in pattern.finditer(text):
        mentions.extend(_resolve(match.group(0), nodes))
    mentions = list(dict.fromkeys(mentions))
    normalized_query = re.sub(r"\s+", "", text.casefold())
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
        rows = ["ローカルルートグラフ（本文に含まれる接続条件と一致。CSV記載に基づく）:"]
        for source, target, info in condition_matches[:12]:
            rows.append(f"- {_node(source, nodes)['id']} → {_node(target, nodes)['id']}")
            rows.append(f"  接続条件: {info.get('condition', '')}")
        return "\n".join(rows)
    if len(mentions) == 1:
        current = mentions[0]
        choices = graph.get(current, [])
        rows = [f"ローカルルートグラフ: {nodes[current].get('title') or current} からの接続"]
        for target in choices[:12]:
            rows.append(f"- {nodes[target].get('title') or target}")
            for info in edge_info.get((current, target), []):
                rows.append(f"  {info.get('condition', '')} [{info.get('certainty', 'Unknown')}/{info.get('availability', 'Unknown')}]")
        return "\n".join(rows)
    if len(mentions) >= 2:
        result = search_routes(mentions[0], mentions[1], base_dir=base_dir,
                               include_unknown=True, include_uncertain=True,
                               include_needs_review=True)
        rows = ["ローカルルートグラフ（未確認接続を含む参考情報）:"]
        for route in result.get("routes", []):
            rows.append(" → ".join(node.get("title") or node["id"] for node in route))
        return "\n".join(rows)
    return "ルートデータは利用可能ですが、対象となるLevelを特定できませんでした。"
