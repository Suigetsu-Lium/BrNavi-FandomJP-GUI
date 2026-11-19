import csv
import json

from modules.route_migration import migrate_legacy_route_csvs
from modules.route_schema import EDGE_FIELDS, EVIDENCE_FIELDS, NODE_FIELDS, validate_csv


def _write(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_migration_is_non_destructive_and_keeps_article_evidence(tmp_path):
    nodes = tmp_path / "legacy_nodes.csv"
    edges = tmp_path / "legacy_edges.csv"
    out = tmp_path / "v2"
    _write(nodes, ["Id", "Label", "Comprehension", "Danger"], [
        {"Id": "Level 8", "Label": "Level 8", "Comprehension": "50%", "Danger": "2/5"},
    ])
    _write(edges, ["Source", "Target", "Type", "Category", "Condition", "Certainty", "Evidence"], [
        {"Source": "Level 9", "Target": "Level 8", "Type": "Directed", "Category": "Entrance", "Condition": "From Level 9 to Level 8", "Certainty": "Confirmed", "Evidence": "SectionInference"},
        {"Source": "Level 8", "Target": "Level 9", "Type": "Directed", "Category": "Exit", "Condition": "Exit to Level 9", "Certainty": "Confirmed", "Evidence": "ExplicitTransition"},
        {"Source": "Level 8", "Target": "Level 9", "Type": "Directed", "Category": "Exit", "Condition": "A second article statement", "Certainty": "Uncertain", "Evidence": "SectionInference"},
    ])
    before_nodes, before_edges = nodes.read_bytes(), edges.read_bytes()

    result = migrate_legacy_route_csvs(nodes, edges, out)

    assert result["nodes"] == 2
    assert result["edges"] == 2
    assert result["evidence"] == 3
    assert nodes.read_bytes() == before_nodes
    assert edges.read_bytes() == before_edges
    assert validate_csv(out / "backrooms_nodes_v2.csv", NODE_FIELDS) == 2
    assert validate_csv(out / "backrooms_edges_v2.csv", EDGE_FIELDS) == 2
    assert validate_csv(out / "backrooms_evidence_v2.csv", EVIDENCE_FIELDS) == 3

    with (out / "backrooms_evidence_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        evidence = list(csv.DictReader(stream))
    entrance = next(item for item in evidence if item["Relation_Type"] == "Entrance")
    assert entrance["Source_Raw"] == "Level 9"
    assert entrance["Target_Raw"] == "Level 8"
    assert entrance["Article_ID"] != ""
    assert {item["Certainty"] for item in evidence} == {"Confirmed", "Uncertain"}
    assert {item["Extract_Method"] for item in evidence} == {"SectionInference", "ExplicitTransition"}
    with (out / "backrooms_edges_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        migrated_edges = list(csv.DictReader(stream))
    reverse_edge = next(item for item in migrated_edges if item["Source_ID"] == entrance["Article_ID"])
    assert reverse_edge["Certainty"] == "Unknown"  # conflicting evidence stays unresolved
    assert all(item["Probability"] == "Unknown" for item in evidence)


def test_migration_does_not_infer_probabilities_or_merge_distinct_evidence(tmp_path):
    nodes = tmp_path / "nodes.csv"
    edges = tmp_path / "edges.csv"
    _write(nodes, ["Id", "Label"], [{"Id": "Level 1", "Label": "Level 1"}])
    _write(edges, ["Source", "Target", "Type", "Category", "Condition"], [
        {"Source": "Level 1", "Target": "Level 2", "Type": "Directed", "Category": "Exit", "Condition": "often"},
        {"Source": "Level 1", "Target": "Level 2", "Type": "Directed", "Category": "Exit", "Condition": "rarely"},
    ])
    result = migrate_legacy_route_csvs(nodes, edges, tmp_path / "out")
    assert result["edges"] == 1
    assert result["evidence"] == 2
    with (tmp_path / "out" / "backrooms_edges_v2.csv").open(encoding="utf-8-sig", newline="") as stream:
        edge = next(csv.DictReader(stream))
    assert edge["Probability"] == "Unknown"
    assert edge["Review_Status"] == "NeedsReview"
