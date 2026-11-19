import csv

import pytest

from modules.route_schema import (
    EDGE_FIELDS,
    ENUM_VALUES,
    EVIDENCE_FIELDS,
    NODE_FIELDS,
    SchemaError,
    validate_csv,
    validate_row,
)


def test_v2_tables_keep_route_facts_separate_from_article_evidence():
    assert "Availability" in EDGE_FIELDS
    assert "Certainty" in EDGE_FIELDS
    assert "Evidence_Type" in EDGE_FIELDS
    assert "Probability" in EDGE_FIELDS
    assert "Article_ID" in EVIDENCE_FIELDS
    assert "Raw_Text" in EVIDENCE_FIELDS
    assert "Relation_Type" in EVIDENCE_FIELDS
    assert "Canonical_URL" in NODE_FIELDS


def test_certainty_does_not_imply_probability():
    row = {field: "" for field in EDGE_FIELDS}
    row.update({
        "Edge_ID": "edge:test",
        "Source_ID": "node:a",
        "Target_ID": "node:b",
        "Direction": "Directed",
        "Certainty": "Confirmed",
        "Availability": "Available",
        "Evidence_Type": "Actual",
        "Probability": "Unknown",
        "Review_Status": "NeedsReview",
    })
    validate_row(row, EDGE_FIELDS)
    assert row["Probability"] == "Unknown"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("Certainty", "Common"),
        ("Availability", "Rumor"),
        ("Evidence_Type", "Uncertain"),
        ("Probability", "Confirmed"),
        ("Review_Status", "Maybe"),
    ],
)
def test_independent_enums_reject_values_from_other_dimensions(field, value):
    row = {name: "" for name in EDGE_FIELDS}
    row[field] = value
    with pytest.raises(SchemaError):
        validate_row(row, EDGE_FIELDS)


def test_validate_csv_checks_exact_header_and_rows(tmp_path):
    path = tmp_path / "edges.csv"
    row = {field: "" for field in EDGE_FIELDS}
    row.update({
        "Edge_ID": "edge:1",
        "Source_ID": "node:a",
        "Target_ID": "node:b",
        "Direction": "Directed",
        "Certainty": "Uncertain",
        "Availability": "Unknown",
        "Evidence_Type": "Unknown",
        "Probability": "Unknown",
        "Review_Status": "NeedsReview",
    })
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EDGE_FIELDS)
        writer.writeheader()
        writer.writerow(row)
    assert validate_csv(path, EDGE_FIELDS) == 1


def test_validate_csv_rejects_wrong_header(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("Source,Target\nA,B\n", encoding="utf-8")
    with pytest.raises(SchemaError, match="header mismatch"):
        validate_csv(path, EDGE_FIELDS)


def test_enum_contracts_are_nonempty():
    assert all(values for values in ENUM_VALUES.values())
