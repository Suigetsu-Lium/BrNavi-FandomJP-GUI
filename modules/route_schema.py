"""Canonical three-file route dataset schema and integrity validation."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Mapping

SCHEMA_VERSION = "2"
NODE_FILENAME = "backrooms_nodes.csv"
EDGE_FILENAME = "backrooms_edges.csv"
EVIDENCE_FILENAME = "backrooms_evidence.csv"

NODE_FIELDS = (
    "Node_ID", "Canonical_Title", "Canonical_URL", "Aliases",
    "Comprehension", "Danger", "Node_Type", "Data_Status",
)
EDGE_FIELDS = (
    "Edge_ID", "Source_ID", "Target_ID", "Direction", "Certainty",
    "Availability", "Evidence_Type", "Probability", "Condition", "Review_Status",
)
EVIDENCE_FIELDS = (
    "Evidence_ID", "Edge_ID", "Article_ID", "Relation_Type", "Source_Raw",
    "Target_Raw", "Raw_Text", "Extract_Method", "Confidence", "Certainty",
    "Availability", "Evidence_Type", "Probability",
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
    """Raised when a route CSV set violates its contract."""


def validate_row(row: Mapping[str, object], fields: Iterable[str], *, allow_extra: bool = False) -> None:
    fields = tuple(fields)
    missing = [field for field in fields if field not in row]
    if missing:
        raise SchemaError(f"Missing required columns: {', '.join(missing)}")
    if not allow_extra:
        extra = [str(field) for field in row if field not in fields]
        if extra:
            raise SchemaError(f"Unexpected columns: {', '.join(extra)}")
    required_values = {
        "Node_ID", "Canonical_Title", "Node_Type", "Data_Status",
        "Edge_ID", "Source_ID", "Target_ID", "Direction", "Certainty",
        "Availability", "Evidence_Type", "Probability", "Review_Status",
        "Evidence_ID", "Article_ID", "Relation_Type", "Extract_Method",
    }
    for field in fields:
        value = str(row.get(field, "") or "").strip()
        if field in required_values and not value:
            raise SchemaError(f"{field} is required")
        allowed = ENUM_VALUES.get(field)
        if allowed and value and value not in allowed:
            raise SchemaError(f"Invalid {field}: {value!r}; expected one of {sorted(allowed)}")
    if "Aliases" in fields:
        try:
            aliases = json.loads(str(row.get("Aliases") or "[]"))
        except (TypeError, ValueError) as error:
            raise SchemaError(f"Aliases must be valid JSON: {error}") from error
        if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
            raise SchemaError("Aliases must be a JSON array of strings")


def read_csv_rows(path: str | Path, fields: Iterable[str]) -> list[dict[str, str]]:
    path = Path(path)
    fields = tuple(fields)
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        actual = tuple(reader.fieldnames or ())
        if actual != fields:
            legacy = actual in {
                ("Id", "Label", "Comprehension", "Danger"),
                ("Source", "Target", "Type", "Category", "Condition", "Certainty",
                 "SourceKind", "TargetKind", "Evidence"),
            }
            hint = " Legacy CSV detected; stop and run explicit migration to a separate directory." if legacy else ""
            raise SchemaError(f"{path.name}: header mismatch; expected {fields}, got {actual}.{hint}")
        rows = []
        seen = set()
        id_field = fields[0]
        for line_number, row in enumerate(reader, start=2):
            try:
                validate_row(row, fields)
                identifier = str(row[id_field]).strip()
                if identifier in seen:
                    raise SchemaError(f"duplicate {id_field}: {identifier}")
                seen.add(identifier)
            except SchemaError as error:
                raise SchemaError(f"{path.name}:{line_number}: {error}") from error
            rows.append(dict(row))
    return rows


def validate_csv(path: str | Path, fields: Iterable[str]) -> int:
    return len(read_csv_rows(path, fields))


def validate_dataset(directory: str | Path, *, rows_by_name=None) -> dict[str, list[dict[str, str]]]:
    root = Path(directory)
    specs = {
        NODE_FILENAME: NODE_FIELDS,
        EDGE_FILENAME: EDGE_FIELDS,
        EVIDENCE_FILENAME: EVIDENCE_FIELDS,
    }
    rows = {}
    for filename, fields in specs.items():
        if rows_by_name is not None and filename in rows_by_name:
            current = [dict(row) for row in rows_by_name[filename]]
            seen = set()
            for index, row in enumerate(current, 2):
                try:
                    validate_row(row, fields)
                    identifier = str(row[fields[0]]).strip()
                    if identifier in seen:
                        raise SchemaError(f"duplicate {fields[0]}: {identifier}")
                    seen.add(identifier)
                except SchemaError as error:
                    raise SchemaError(f"{filename}:{index}: {error}") from error
            rows[filename] = current
        else:
            path = root / filename
            if not path.is_file():
                raise FileNotFoundError(f"Required route CSV is missing: {filename}")
            rows[filename] = read_csv_rows(path, fields)

    node_ids = {row["Node_ID"] for row in rows[NODE_FILENAME]}
    edge_ids = {row["Edge_ID"] for row in rows[EDGE_FILENAME]}
    for index, edge in enumerate(rows[EDGE_FILENAME], 2):
        if edge["Source_ID"] not in node_ids or edge["Target_ID"] not in node_ids:
            raise SchemaError(
                f"{EDGE_FILENAME}:{index}: Source_ID/Target_ID reference a missing Node_ID"
            )
        if edge["Source_ID"] == edge["Target_ID"]:
            raise SchemaError(f"{EDGE_FILENAME}:{index}: self-referencing edge is invalid")
    evidence_edge_ids = set()
    for index, evidence in enumerate(rows[EVIDENCE_FILENAME], 2):
        if evidence["Edge_ID"] not in edge_ids:
            raise SchemaError(f"{EVIDENCE_FILENAME}:{index}: Edge_ID reference does not exist")
        if evidence["Article_ID"] not in node_ids:
            raise SchemaError(f"{EVIDENCE_FILENAME}:{index}: Article_ID reference does not exist")
        evidence_edge_ids.add(evidence["Edge_ID"])
    missing_evidence = edge_ids - evidence_edge_ids
    if missing_evidence:
        raise SchemaError(
            "Every Edge must have at least one Evidence row; missing for: "
            + ", ".join(sorted(missing_evidence))
        )
    return rows
