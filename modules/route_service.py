"""Canonical route graph loading, filtering, and shortest-path search."""
import csv
import json
import re
from collections import defaultdict, deque
from pathlib import Path

from modules.runtime_paths import get_application_dir
from modules.wiki_markup import format_wiki_links
from modules.route_schema import (
    NODE_FILENAME, EDGE_FILENAME, EVIDENCE_FILENAME,
    validate_dataset, SchemaError,
)

BASE_DIR = get_application_dir()


def _clean(value):
    return str(value or "").strip().strip('"\'「」『』')


def load_route_graph(base_dir=None):
    """Load only the canonical three-file dataset; never fall back to legacy CSVs."""
    root = Path(base_dir) if base_dir else BASE_DIR
    canonical = (root / NODE_FILENAME, root / EDGE_FILENAME, root / EVIDENCE_FILENAME)
    legacy = (root / NODE_FILENAME, root / EDGE_FILENAME)
    if not all(path.is_file() for path in canonical):
        existing_legacy = [path for path in legacy if path.is_file()]
        if existing_legacy:
            for path in existing_legacy:
                with path.open("r", encoding="utf-8-sig", newline="") as stream:
                    header = tuple(next(csv.reader(stream), ()))
                if header and header[0] in {"Id", "Source"}:
                    raise SchemaError(
                        f"Legacy route CSV detected in {path.name}. Automatic migration is disabled; "
                        "use the explicit migration command with a separate output directory."
                    )
        missing = [path.name for path in canonical if not path.is_file()]
        raise FileNotFoundError("Required route CSV set is incomplete: " + ", ".join(missing))

    dataset = validate_dataset(root)
    nodes = {}
    for row in dataset[NODE_FILENAME]:
        aliases = json.loads(row["Aliases"] or "[]")
        nodes[row["Node_ID"]] = {
            "title": _clean(row["Canonical_Title"]),
            "danger": _clean(row["Danger"]),
            "comprehension": _clean(row["Comprehension"]),
            "aliases": [_clean(value) for value in aliases if _clean(value)],
            "url": _clean(row["Canonical_URL"]),
            "node_type": _clean(row["Node_Type"]) or "Unknown",
            "data_status": _clean(row["Data_Status"]) or "NeedsReview",
            "schema": "evidence",
        }

    evidence_by_edge = defaultdict(list)
    for row in dataset[EVIDENCE_FILENAME]:
        evidence_by_edge[row["Edge_ID"]].append({
            "article_id": _clean(row["Article_ID"]),
            "article_title": nodes.get(_clean(row["Article_ID"]), {}).get("title", ""),
            "relation_type": _clean(row["Relation_Type"]),
            "source_raw": _clean(row["Source_Raw"]),
            "target_raw": _clean(row["Target_Raw"]),
            "condition": _clean(row["Raw_Text"]),
            "extract_method": _clean(row["Extract_Method"]),
            "confidence": _clean(row["Confidence"]),
            "certainty": _clean(row["Certainty"]) or "Unknown",
            "availability": _clean(row["Availability"]) or "Unknown",
            "evidence_type": _clean(row["Evidence_Type"]) or "Unknown",
            "probability": _clean(row["Probability"]) or "Unknown",
        })

    graph = defaultdict(list)
    edge_info = defaultdict(list)
    for row in dataset[EDGE_FILENAME]:
        source, target = row["Source_ID"], row["Target_ID"]
        edge = {
            "edge_id": row["Edge_ID"],
            "type": row["Direction"],
            "category": "",
            "condition": _clean(row["Condition"]),
            "certainty": row["Certainty"] or "Unknown",
            "availability": row["Availability"] or "Unknown",
            "evidence_type": row["Evidence_Type"] or "Unknown",
            "probability": row["Probability"] or "Unknown",
            "review_status": row["Review_Status"] or "NeedsReview",
            "evidence": evidence_by_edge.get(row["Edge_ID"], []),
            "schema": "evidence",
        }
        if target not in graph[source]:
            graph[source].append(target)
        edge_info[(source, target)].append(edge)
        if row["Direction"] == "Bidirectional":
            if source not in graph[target]:
                graph[target].append(source)
            reverse = dict(edge)
            reverse["reversed"] = True
            edge_info[(target, source)].append(reverse)
    return nodes, dict(graph), dict(edge_info)


def _resolve(query, nodes):
    q = re.sub(r"\s+", " ", str(query or "").strip()).casefold()
    if not q:
        return []

    def active_items():
        return (
            (key, value) for key, value in nodes.items()
            if value.get("data_status") != "Deprecated"
        )

    def labels(key, item):
        return [key, item.get("title", ""), *item.get("aliases", [])]

    exact_ids = [key for key, _item in active_items() if key.casefold() == q]
    if exact_ids:
        return exact_ids
    exact_labels = [
        key for key, value in active_items()
        if any(label.casefold() == q for label in labels(key, value) if label)
    ]
    if exact_labels:
        return exact_labels
    if q.isdigit():
        level = f"level {q}"
        normal = [
            key for key, value in active_items()
            if any(label.casefold() == level for label in labels(key, value) if label)
        ]
        if normal:
            return normal
    return [
        key for key, value in active_items()
        if any(q in label.casefold() for label in labels(key, value) if label)
    ]


def resolve_node_candidates(query, nodes):
    return _resolve(query, nodes)


def _node(node_id, nodes):
    item = nodes.get(node_id, {})
    aliases = item.get("aliases", [])
    display_id = (aliases[0] if aliases else item.get("title") or node_id)
    return {"id": display_id, "node_id": node_id, **item}


def _edge_allowed(info, *, include_uncertain=True, include_unknown=True,
                  include_rumor=False, include_closed=False, include_needs_review=True):
    if info.get("evidence_type") in {"False", "Meta"}:
        return False
    if info.get("review_status") == "Rejected":
        return False
    if info.get("evidence_type") == "Rumor" and not include_rumor:
        return False
    if info.get("certainty") == "Uncertain" and not include_uncertain:
        return False
    if info.get("certainty") == "Unknown" and not include_unknown:
        return False
    if info.get("availability") in {"Closed", "Disappeared"} and not include_closed:
        return False
    if info.get("availability") == "Unknown" and not include_unknown:
        return False
    if info.get("review_status") == "NeedsReview" and not include_needs_review:
        return False
    return True


def _evidence_allowed(evidence, *, include_uncertain, include_unknown,
                      include_rumor, include_closed):
    if evidence.get("evidence_type") in {"False", "Meta"}:
        return False
    if evidence.get("evidence_type") == "Rumor" and not include_rumor:
        return False
    if evidence.get("certainty") == "Uncertain" and not include_uncertain:
        return False
    if evidence.get("certainty") == "Unknown" and not include_unknown:
        return False
    if evidence.get("availability") in {"Closed", "Disappeared"} and not include_closed:
        return False
    if evidence.get("availability") == "Unknown" and not include_unknown:
        return False
    return True


def _filtered_edges(nodes, all_edge_info, *, include_uncertain, include_unknown,
                    include_rumor, include_closed, include_needs_review):
    selected = {}
    for pair, infos in all_edge_info.items():
        if (nodes.get(pair[0], {}).get("data_status") == "Deprecated"
                or nodes.get(pair[1], {}).get("data_status") == "Deprecated"):
            continue
        accepted = []
        for info in infos:
            if not _edge_allowed(
                info, include_uncertain=include_uncertain, include_unknown=include_unknown,
                include_rumor=include_rumor, include_closed=include_closed,
                include_needs_review=include_needs_review,
            ):
                continue
            evidence_rows = info.get("evidence", [])
            eligible_evidence = [evidence for evidence in evidence_rows if _evidence_allowed(
                evidence, include_uncertain=include_uncertain,
                include_unknown=include_unknown, include_rumor=include_rumor,
                include_closed=include_closed,
            )]
            if evidence_rows and not eligible_evidence:
                continue
            accepted.append({**info, "evidence": eligible_evidence})
        if accepted:
            selected[pair] = accepted
    return selected


def search_routes(start_query, goal_query, *, base_dir=None, max_results=8, max_depth=30,
                  include_uncertain=True, include_unknown=True, include_rumor=False,
                  include_closed=False, include_needs_review=True):
    try:
        nodes, _all_graph, all_edge_info = load_route_graph(base_dir)
    except (OSError, csv.Error, UnicodeError, ValueError) as error:
        return {"error": f"ルートCSVを読み込めません: {error}",
                "start_candidates": [], "goal_candidates": [], "routes": []}
    if not all_edge_info:
        return {"error": "ルートCSVに接続データがありません。",
                "start_candidates": [], "goal_candidates": [], "routes": []}

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

    selected_info = _filtered_edges(
        nodes, all_edge_info, include_uncertain=include_uncertain,
        include_unknown=include_unknown, include_rumor=include_rumor,
        include_closed=include_closed, include_needs_review=include_needs_review,
    )
    graph = defaultdict(list)
    for source, target in selected_info:
        graph[source].append(target)

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
        result["filters"] = _filter_result(include_uncertain, include_unknown, include_rumor,
                                           include_closed, include_needs_review)
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
    result["filters"] = _filter_result(include_uncertain, include_unknown, include_rumor,
                                       include_closed, include_needs_review)
    return result


def _filter_result(include_uncertain, include_unknown, include_rumor, include_closed, include_needs_review):
    return {
        "include_uncertain": include_uncertain, "include_unknown": include_unknown,
        "include_rumor": include_rumor, "include_closed": include_closed,
        "include_needs_review": include_needs_review,
        "always_excluded": ["False", "Meta", "Rejected"],
    }


def build_route_context(query, *, base_dir=None):
    text = str(query or "")
    if not re.search(r"経路|ルート|出口|入口|移動|行き来|到達|どこ|旅行|Level\s*[-0-9A-Za-z.]+", text, re.I):
        return ""
    try:
        nodes, _graph, all_edge_info = load_route_graph(base_dir)
    except (OSError, csv.Error, UnicodeError, ValueError) as error:
        return f"ルートCSVを読み込めません: {error}"
    if not all_edge_info:
        return "ローカルルートネットワークCSVに接続データがありません。"
    selected_info = _filtered_edges(
        nodes, all_edge_info, include_uncertain=True, include_unknown=True,
        include_rumor=False, include_closed=False, include_needs_review=True,
    )
    graph = defaultdict(list)
    for source, target in selected_info:
        graph[source].append(target)

    mentions = []
    pattern = re.compile(r"(?:Level\s*[-0-9A-Za-z.]+(?:\s*η)?|The\s+[A-Za-z][A-Za-z ]+)", re.I)
    for match in pattern.finditer(text):
        mentions.extend(_resolve(match.group(0), nodes))
    mentions = list(dict.fromkeys(mentions))
    normalized_query = re.sub(r"\s+", "", text.casefold())
    condition_matches = []
    for (source, target), infos in selected_info.items():
        if mentions and not ({source, target} & set(mentions)):
            continue
        for info in infos:
            edge_condition = _clean(info.get("condition"))
            normalized_edge_condition = re.sub(r"\s+", "", edge_condition.casefold())
            matched_evidence = [
                evidence for evidence in info.get("evidence", [])
                if len(re.sub(r"\s+", "", _clean(evidence.get("condition")).casefold())) >= 4
                and re.sub(r"\s+", "", _clean(evidence.get("condition")).casefold()) in normalized_query
            ]
            if (
                len(normalized_edge_condition) >= 4
                and normalized_edge_condition in normalized_query
            ) or matched_evidence:
                condition_matches.append((source, target, {**info, "evidence": matched_evidence or info.get("evidence", [])}))

    def append_evidence(rows, info):
        for evidence in info.get("evidence", [])[:5]:
            article = evidence.get("article_title") or evidence.get("article_id") or "記事不明"
            relation = evidence.get("relation_type") or "区分不明"
            raw_text = evidence.get("condition") or ""
            rows.append(f"  根拠記事: {article} ({relation})")
            if raw_text:
                rows.append(f"    原文: {format_wiki_links(raw_text)}")

    if condition_matches:
        rows = ["ローカルルートグラフ（未確認情報を含む参考情報。実際の到達・安全性は保証しません）:"]
        for source, target, info in condition_matches[:12]:
            rows.append(f"- {_node(source, nodes)['id']} → {_node(target, nodes)['id']}")
            rows.append(f"  状態: {info.get('certainty', 'Unknown')}/{info.get('availability', 'Unknown')}/{info.get('review_status', 'NeedsReview')}")
            if info.get("condition"):
                rows.append(f"  接続条件: {format_wiki_links(info['condition'])}")
            append_evidence(rows, info)
        return "\n".join(rows)
    if len(mentions) == 1:
        current = mentions[0]
        rows = [f"ローカルルートグラフ: {nodes[current].get('title') or current} からの接続（未確認情報を含む参考情報。実際の到達・安全性は保証しません）"]
        for target in graph.get(current, [])[:12]:
            rows.append(f"- {nodes[target].get('title') or target}")
            for info in selected_info.get((current, target), []):
                rows.append(f"  状態: {info.get('certainty', 'Unknown')}/{info.get('availability', 'Unknown')}/{info.get('review_status', 'NeedsReview')}")
                if info.get("condition"):
                    rows.append(f"  接続条件: {format_wiki_links(info['condition'])}")
                append_evidence(rows, info)
        return "\n".join(rows)
    if len(mentions) >= 2:
        result = search_routes(mentions[0], mentions[1], base_dir=base_dir)
        rows = ["ローカルルートグラフ（未確認情報を含む参考情報。実際の到達・安全性は保証しません）:"]
        for route in result.get("routes", []):
            rows.append(" → ".join(node.get("title") or node["id"] for node in route))
            for source_node, target_node in zip(route, route[1:]):
                source_id, target_id = source_node["node_id"], target_node["node_id"]
                for info in selected_info.get((source_id, target_id), []):
                    rows.append(f"  状態: {info.get('certainty', 'Unknown')}/{info.get('availability', 'Unknown')}/{info.get('review_status', 'NeedsReview')}")
                    if info.get("condition"):
                        rows.append(f"  接続条件: {format_wiki_links(info['condition'])}")
                    append_evidence(rows, info)
        if not result.get("routes"):
            rows.append(result.get("message") or result.get("error") or "該当経路はありません。")
        return "\n".join(rows)
    return "ルートデータは利用可能ですが、対象となるLevelを特定できませんでした。"
