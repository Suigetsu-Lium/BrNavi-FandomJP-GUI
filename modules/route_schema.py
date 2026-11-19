"""Versioned CSV contracts for the evidence-based route database.

This module defines the v2 schema without changing or migrating legacy CSV files.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA_VERSION = "2"

NODE_FIELDS = (
    "Node_ID",
    "Canonical_Title",
    "Canonical_URL",
    "Aliases",
    "Comprehension",
    "Danger",
    "Node_Type",
    "Data_Status",
)

EDGE_FIELDS = (
    "Edge_ID",
    "Source_ID",
    "Target_ID",
    "Direction",
    "Certainty",
    "Availability",
    "Evidence_Type",
    "Probability",
    "Condition",
    "Review_Status",
)

EVIDENCE_FIELDS = (
    "Evidence_ID",
    "Edge_ID",
    "Article_ID",
    "Relation_Type",
    "Source_Raw",
    "Target_Raw",
    "Raw_Text",
    "Extract_Method",
    "Confidence",
    "Certainty",
    "Availability",
    "Evidence_Type",
    "Probability",
)

ENUM_VALUES = {
    "Direction": frozenset({"Directed", "Bidirectional", "Unknown"}),
    "Certainty": frozenset({"Confirmed", "Uncertain", "Unknown"}),
    "Availability": frozenset({"Available", "Closed", "Disappeared", "Unknown"}),
    "Evidence_Type": frozenset({"Actual", "Rumor", "False", "Meta", "Unknown"}),
    "Probability": frozenset({"Common", "Uncommon", "Rare", "ExtremelyRare", "Unknown"}),
    "Review_Status": frozenset({"Reviewed", "NeedsReview", "Rejected"}),
    "Relation_Type": frozenset({"Entrance", "Exit"}),
    "Node_Type": frozenset({"Level", "NamedLocation", "Frontrooms", "Other", "Unknown"}),
    "Data_Status": frozenset({"Active", "Deprecated", "NeedsReview"}),
}


class SchemaError(ValueError):
    """Raised when a v2 route CSV violates its declared contract."""


def validate_row(row: Mapping[str, object], fields: Iterable[str], *, allow_extra: bool = False) -> None:
    fields = tuple(fields)
    missing = [field for field in fields if field not in row]
    if missing:
        raise SchemaError(f"Missing required columns: {', '.join(missing)}")
    if not allow_extra:
        extra = [field for field in row if field not in fields]
        if extra:
            raise SchemaError(f"Unexpected columns: {', '.join(extra)}")
    for field, allowed in ENUM_VALUES.items():
        if field in fields:
            value = str(row.get(field, "") or "").strip()
            if value and value not in allowed:
                raise SchemaError(f"Invalid {field}: {value!r}; expected one of {sorted(allowed)}")


def validate_csv(path: str | Path, fields: Iterable[str]) -> int:
    """Validate a v2 CSV header and enum values; return its data-row count."""
    path = Path(path)
    fields = tuple(fields)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != fields:
            raise SchemaError(
                f"{path.name}: header mismatch; expected {fields}, got {tuple(reader.fieldnames or ())}"
            )
        count = 0
        for line_number, row in enumerate(reader, start=2):
            try:
                validate_row(row, fields)
            except SchemaError as error:
                raise SchemaError(f"{path.name}:{line_number}: {error}") from error
            count += 1
    return count
