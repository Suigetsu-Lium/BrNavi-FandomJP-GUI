import csv
import json

from modules.route_migration import migrate_legacy_route_csvs
from modules.route_service import load_route_graph, search_routes


def _write(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _make_v2(root, *, certainty="Confirmed", availability="Available",
             evidence_type="Actual", review="Reviewed"):
    nodes = [
        {"Node_ID": "n-a", "Canonical_Title": "Level 1", "Canonical_URL": "",
         "Aliases": '["Level One"]', "Comprehension": "50%", "Danger": "2/5",
         "Node_Type": "Level", "Data_Status": "Active"},
        {"Node_ID": "n-b", "Canonical_Title": "Level 2", "Canonical_URL": "",
         "Aliases": "[]", "Comprehension": "", "Danger": "",
         "Node_Type": "Level", "Data_Status": "Active"},
    ]
    edges = [{
        "Edge_ID": "e-1", "Source_ID": "n-a", "Target_ID": "n-b",
        "Direction": "Directed", "Certainty": certainty, "Availability": availability,
        "Evidence_Type": evidence_type, "Probability": "Unknown",
        "Condition": "Use the marked door", "Review_Status": review,
    }]
    evidence = [{
        "Evidence_ID": "ev-1", "Edge_ID": "e-1", "Article_ID": "n-a",
        "Relation_Type": "Exit", "Source_Raw": "Level 1", "Target_Raw": "Level 2",
        "Raw_Text": "Use the marked door", "Extract_Method": "ExplicitTransition",
        "Confidence": "0.95", "Certainty": certainty, "Availability": availability,
        "Evidence_Type": evidence_type, "Probability": "Unknown",
    }]
    _write(root / "backrooms_nodes_v2.csv",
           ["Node_ID", "Canonical_Title", "Canonical_URL", "Aliases", "Comprehension", "Danger", "Node_Type", "Data_Status"], nodes)
    _write(root / "backrooms_edges_v2.csv",
           ["Edge_ID", "Source_ID", "Target_ID", "Direction", "Certainty", "Availability", "Evidence_Type", "Probability", "Condition", "Review_Status"], edges)
    _write(root / "backrooms_evidence_v2.csv",
           ["Evidence_ID", "Edge_ID", "Article_ID", "Relation_Type", "Source_Raw", "Target_Raw", "Raw_Text", "Extract_Method", "Confidence", "Certainty", "Availability", "Evidence_Type", "Probability"], evidence)


def test_v2_loader_resolves_aliases_and_preserves_evidence(tmp_path):
    _make_v2(tmp_path)
    nodes, graph, edge_info = load_route_graph(tmp_path)
    assert graph == {"n-a": ["n-b"]}
    result = search_routes("Level One", "Level 2", base_dir=tmp_path)
    assert not result.get("error")
    assert result["routes"][0][0]["id"] == "Level One"
    info = result["edge_info"]["Level One\nLevel 2"][0]
    assert info["evidence"][0]["article_id"] == "n-a"
    assert info["evidence"][0]["relation_type"] == "Exit"


def test_v2_default_is_confirmed_available_reviewed_only(tmp_path):
    _make_v2(tmp_path, certainty="Unknown", availability="Unknown", review="NeedsReview")
    result = search_routes("Level 1", "Level 2", base_dir=tmp_path)
    assert result["routes"] == []
    included = search_routes("Level 1", "Level 2", base_dir=tmp_path,
                             include_unknown=True, include_needs_review=True)
    assert len(included["routes"]) == 1


def test_v2_false_and_meta_evidence_never_enters_routes(tmp_path):
    _make_v2(tmp_path, evidence_type="False")
    result = search_routes("Level 1", "Level 2", base_dir=tmp_path,
                           include_unknown=True, include_needs_review=True,
                           include_rumor=True, include_closed=True)
    assert result["routes"] == []


def test_v2_closed_and_uncertain_require_explicit_filters(tmp_path):
    _make_v2(tmp_path, certainty="Uncertain", availability="Closed")
    assert search_routes("Level 1", "Level 2", base_dir=tmp_path,
                         include_uncertain=True, include_unknown=True,
                         include_needs_review=True)["routes"] == []
    result = search_routes("Level 1", "Level 2", base_dir=tmp_path,
                           include_uncertain=True, include_closed=True,
                           include_needs_review=True)
    assert len(result["routes"]) == 1


def test_migration_adds_old_ids_as_searchable_aliases(tmp_path):
    nodes = tmp_path / "nodes.csv"
    edges = tmp_path / "edges.csv"
    _write(nodes, ["Id", "Label", "Comprehension", "Danger"], [
        {"Id": "Level 1", "Label": "Level 1: The Lobby", "Comprehension": "", "Danger": ""},
        {"Id": "Level 2", "Label": "Level 2", "Comprehension": "", "Danger": ""},
    ])
    _write(edges, ["Source", "Target", "Type", "Category", "Condition"], [
        {"Source": "Level 1", "Target": "Level 2", "Type": "Directed", "Category": "Exit", "Condition": "Door"},
    ])
    migrate_legacy_route_csvs(nodes, edges, tmp_path / "v2")
    _, _, _ = load_route_graph(tmp_path / "v2")
    result = search_routes("Level 1", "Level 2", base_dir=tmp_path / "v2",
                           include_unknown=True, include_needs_review=True)
    assert len(result["start_candidates"]) == 1
    assert result["start_candidates"][0]["title"] == "Level 1: The Lobby"
