"""Explicit, non-destructive migration from legacy CSVs to the canonical evidence model."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from pathlib import Path

from modules.route_schema import (
    EDGE_FIELDS, EVIDENCE_FIELDS, NODE_FIELDS,
    NODE_FILENAME, EDGE_FILENAME, EVIDENCE_FILENAME,
    validate_dataset,
)


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
    """Write canonical route files to a separate directory; never modify either input.

    Legacy classifications are not guessed. Missing dimensions become Unknown,
    and every imported edge/evidence row is marked NeedsReview.
    """
    nodes_path, edges_path, output_dir = Path(nodes_path), Path(edges_path), Path(output_dir)
    if not nodes_path.is_file() or not edges_path.is_file():
        raise FileNotFoundError("Both legacy Node and Edge CSV inputs are required.")
    if nodes_path.resolve() == edges_path.resolve():
        raise ValueError("Legacy Node and Edge inputs must be different files.")
    legacy_node_fields = ("Id", "Label", "Comprehension", "Danger")
    legacy_edge_fields = (
        "Source", "Target", "Type", "Category", "Condition", "Certainty",
        "SourceKind", "TargetKind", "Evidence",
    )
    for source_path, expected_fields in (
        (nodes_path, legacy_node_fields), (edges_path, legacy_edge_fields),
    ):
        with source_path.open("r", encoding="utf-8-sig", newline="") as stream:
            actual_fields = tuple(next(csv.reader(stream), ()))
        if actual_fields != expected_fields:
            raise ValueError(
                f"{source_path.name}: unsupported legacy header; expected {expected_fields}, got {actual_fields}"
            )
    output_targets = (
        output_dir / NODE_FILENAME,
        output_dir / EDGE_FILENAME,
        output_dir / EVIDENCE_FILENAME,
    )
    input_directories = {nodes_path.parent.resolve(), edges_path.parent.resolve()}
    if output_dir.resolve() in input_directories:
        raise ValueError("Migration output directory must be separate from both legacy input directories.")
    input_paths = {nodes_path.resolve(), edges_path.resolve()}
    if any(path.resolve() in input_paths for path in output_targets):
        raise ValueError("Migration output must be a separate directory; source CSVs will not be overwritten.")
    existing_outputs = [path.name for path in output_targets if path.exists()]
    if existing_outputs:
        raise FileExistsError(
            "Migration output already exists; refusing to overwrite: "
            + ", ".join(existing_outputs)
        )
    legacy_nodes = _read_csv(nodes_path)
    legacy_edges = _read_csv(edges_path)
    if not legacy_nodes or not legacy_edges:
        raise ValueError("Legacy Node and Edge CSVs must both contain data rows.")
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

    if not nodes or not edges or not evidence:
        raise ValueError("Legacy migration produced no complete Node/Edge/Evidence dataset.")

    outputs = {
        NODE_FILENAME: (NODE_FIELDS, list(nodes.values())),
        EDGE_FILENAME: (EDGE_FIELDS, list(edges.values())),
        EVIDENCE_FILENAME: (EVIDENCE_FIELDS, evidence),
    }
    rows_by_name = {filename: rows for filename, (_fields, rows) in outputs.items()}
    validate_dataset(output_dir, rows_by_name=rows_by_name)

    staging_paths = {}
    published_paths = []
    try:
        for filename, (fields, rows) in outputs.items():
            fd, staging_path = tempfile.mkstemp(
                prefix=".brnavi-route-migration-",
                suffix=".tmp",
                dir=output_dir,
            )
            os.close(fd)
            staging_paths[filename] = Path(staging_path)
            with open(staging_path, "w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

        staged_rows = {}
        for filename, (_fields, _rows) in outputs.items():
            with staging_paths[filename].open("r", encoding="utf-8-sig", newline="") as stream:
                staged_rows[filename] = list(csv.DictReader(stream))
        validate_dataset(output_dir, rows_by_name=staged_rows)

        for filename in outputs:
            destination = output_dir / filename
            os.replace(staging_paths[filename], destination)
            published_paths.append(destination)
    except Exception:
        for destination in published_paths:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        for staging_path in staging_paths.values():
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                pass

    written = {filename: len(rows) for filename, (_fields, rows) in outputs.items()}

    return {
        "nodes": len(nodes),
        "edges": len(edges),
        "evidence": len(evidence),
        "outputs": written,
        "source_nodes": str(nodes_path),
        "source_edges": str(edges_path),
    }
