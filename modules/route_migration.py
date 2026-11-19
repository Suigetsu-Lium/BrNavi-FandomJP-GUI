"""Non-destructive migration from legacy route CSVs to the v2 evidence model."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import OrderedDict
from pathlib import Path

from modules.route_schema import EDGE_FIELDS, EVIDENCE_FIELDS, NODE_FIELDS


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _node_type(label: str) -> str:
    folded = label.casefold()
    if "frontrooms" in folded or "フロントルーム" in label:
        return "Frontrooms"
    if folded.startswith("level "):
        return "Level"
    if folded.startswith("the "):
        return "NamedLocation"
    return "Other" if label else "Unknown"


def migrate_legacy_route_csvs(
    nodes_path: str | Path,
    edges_path: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    """Write v2 files to a separate directory; never modify either input.

    Legacy classifications are not guessed. Missing dimensions become Unknown,
    and every imported edge/evidence row is marked NeedsReview.
    """
    nodes_path, edges_path, output_dir = Path(nodes_path), Path(edges_path), Path(output_dir)
    legacy_nodes = _read_csv(nodes_path)
    legacy_edges = _read_csv(edges_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    nodes: OrderedDict[str, dict[str, str]] = OrderedDict()
    raw_to_id: dict[str, str] = {}

    def ensure_node(raw_id: str, label: str = "") -> str:
        raw_id = str(raw_id or "").strip()
        label = str(label or "").strip() or raw_id
        key = raw_id or label
        if not key:
            return ""
        node_id = raw_to_id.get(key)
        if node_id:
            item = nodes[node_id]
            aliases = json.loads(item["Aliases"])
            if label != item["Canonical_Title"] and label not in aliases:
                aliases.append(label)
                item["Aliases"] = json.dumps(aliases, ensure_ascii=False)
            return node_id
        node_id = _stable_id("legacy", key)
        raw_to_id[key] = node_id
        initial_aliases = [raw_id] if raw_id and raw_id != label else []
        nodes[node_id] = {
            "Node_ID": node_id,
            "Canonical_Title": label,
            "Canonical_URL": "",
            "Aliases": json.dumps(initial_aliases, ensure_ascii=False),
            "Comprehension": "",
            "Danger": "",
            "Node_Type": _node_type(label),
            "Data_Status": "NeedsReview",
        }
        return node_id

    for row in legacy_nodes:
        raw_id = row.get("Id") or row.get("ID") or row.get("Source") or ""
        label = row.get("Label") or row.get("Title") or raw_id
        node_id = ensure_node(raw_id, label)
        if not node_id:
            continue
        item = nodes[node_id]
        item["Comprehension"] = row.get("Comprehension", "") or ""
        item["Danger"] = row.get("Danger", "") or ""

    edges: OrderedDict[str, dict[str, str]] = OrderedDict()
    evidence: list[dict[str, str]] = []
    for index, row in enumerate(legacy_edges, start=1):
        source_raw = str(row.get("Source") or "").strip()
        target_raw = str(row.get("Target") or "").strip()
        if not source_raw or not target_raw:
            continue
        source_id = ensure_node(source_raw)
        target_id = ensure_node(target_raw)
        if not source_id or not target_id or source_id == target_id:
            continue

        direction = "Bidirectional" if str(row.get("Type", "")).casefold() in {
            "bidirectional", "both", "双方向"
        } else "Directed"
        legacy_certainty = str(row.get("Certainty") or "").strip()
        if legacy_certainty not in {"Confirmed", "Uncertain"}:
            legacy_certainty = "Unknown"
        legacy_condition = str(row.get("Condition") or "").strip()
        edge_key = f"{source_id}\n{target_id}\n{direction}"
        edge_id = _stable_id("edge", edge_key)
        if edge_id not in edges:
            edges[edge_id] = {
                "Edge_ID": edge_id,
                "Source_ID": source_id,
                "Target_ID": target_id,
                "Direction": direction,
                "Certainty": legacy_certainty,
                "Availability": "Unknown",
                "Evidence_Type": "Unknown",
                "Probability": "Unknown",
                "Condition": legacy_condition,
                "Review_Status": "NeedsReview",
            }
        else:
            current = edges[edge_id]
            if current["Certainty"] != legacy_certainty:
                current["Certainty"] = "Unknown"
            if current["Condition"] != legacy_condition:
                current["Condition"] = ""

        category = str(row.get("Category") or "").strip()
        relation_type = category if category in {"Entrance", "Exit"} else ""
        article_raw = target_raw if relation_type == "Entrance" else source_raw
        evidence_id = _stable_id(
            "evidence",
            f"{index}\n{article_raw}\n{source_raw}\n{target_raw}\n{row.get('Condition', '')}",
        )
        evidence.append({
            "Evidence_ID": evidence_id,
            "Edge_ID": edge_id,
            "Article_ID": raw_to_id.get(article_raw) or ensure_node(article_raw),
            "Relation_Type": relation_type,
            "Source_Raw": source_raw,
            "Target_Raw": target_raw,
            "Raw_Text": str(row.get("Condition") or ""),
            "Extract_Method": str(row.get("Evidence") or "LegacyCSV"),
            "Confidence": "",
            "Certainty": legacy_certainty,
            "Availability": "Unknown",
            "Evidence_Type": "Unknown",
            "Probability": "Unknown",
        })

    outputs = {
        "backrooms_nodes_v2.csv": (NODE_FIELDS, list(nodes.values())),
        "backrooms_edges_v2.csv": (EDGE_FIELDS, list(edges.values())),
        "backrooms_evidence_v2.csv": (EVIDENCE_FIELDS, evidence),
    }
    written = {}
    for filename, (fields, rows) in outputs.items():
        path = output_dir / filename
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        written[filename] = len(rows)

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "evidence": len(evidence),
        "outputs": written,
        "source_nodes": str(nodes_path),
        "source_edges": str(edges_path),
    }
