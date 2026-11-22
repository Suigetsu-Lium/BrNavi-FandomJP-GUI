# Route CSV data contract

## Current canonical contract

The route dataset consists of exactly these files in the application data root:

- `backrooms_nodes.csv`
- `backrooms_edges.csv`
- `backrooms_evidence.csv`

Do not add a version suffix to these filenames. All three files are required and form one dataset. There is no automatic fallback to the legacy two-file schema and no implicit migration.

### Columns

Nodes:

`Node_ID, Canonical_Title, Canonical_URL, Aliases, Comprehension, Danger, Node_Type, Data_Status`

Edges:

`Edge_ID, Source_ID, Target_ID, Direction, Certainty, Availability, Evidence_Type, Probability, Condition, Review_Status`

Evidence:

`Evidence_ID, Edge_ID, Article_ID, Relation_Type, Source_Raw, Target_Raw, Raw_Text, Extract_Method, Confidence, Certainty, Availability, Evidence_Type, Probability`

The exact column order is enforced by `modules/route_schema.py`. CSV uses UTF-8 with BOM, comma delimiters, and a header row.

### Integrity requirements

- `Node_ID`, `Edge_ID`, and `Evidence_ID` are unique and non-empty within their respective files.
- Every Edge `Source_ID` and `Target_ID` must reference a Node.
- Every Evidence `Edge_ID` must reference an Edge.
- Every Evidence `Article_ID` must reference the Node representing the article that supplied the evidence.
- `Aliases` must be a JSON array of strings.
- Enum values must belong to `ENUM_VALUES`; unknown facts remain `Unknown` or blank where the schema permits.
- Invalid rows, malformed JSON, duplicate IDs, missing files, and broken references fail the entire load. Malformed edges are never silently skipped.

### Extraction and publication

`modules/network.py:process_backrooms_data()` extracts route statements from saved article HTML. It creates the three row sets in memory, validates cross-file integrity, writes temporary staged files, re-reads and validates the staged contents, then replaces all three canonical files using backups and rollback on ordinary replacement exceptions.

The three replacements are not a filesystem-level atomic transaction against power loss or process termination. The loader therefore validates the complete dataset every time and rejects missing or mixed/incomplete sets.

Node IDs use a canonical page URL when available; otherwise they use a conservative normalized title identity. Ambiguous titles are not fuzzy-merged. Evidence rows retain the article association, relation type, raw endpoints, raw text, and extraction method. Multiple articles may provide separate evidence rows for one shared edge.

The extractor does not invent availability, evidence type, probability, or confidence. Initial nodes and edges are `NeedsReview`; unknown dimensions remain Unknown. An Entrance section in a Level 8 article describing entry from Level 9 into Level 8 is stored as Level 9 → Level 8 with `Relation_Type=Entrance`. An Exit section from Level 8 to Level 9 is Level 8 → Level 9 with `Relation_Type=Exit`.

### Legacy data

Legacy headers are detected and cause a clear error. The generator and loader never auto-migrate, silently fall back, overwrite, or delete legacy data. `modules/route_migration.py:migrate_legacy_route_csvs()` is an explicit conversion utility: it reads caller-specified legacy Node/Edge inputs and writes the canonical three filenames into a caller-specified separate output directory. The source files are not modified. Review the generated output before manually switching it into the application data root.

### Search policy

Default search includes `Unknown`, `Uncertain`, and `NeedsReview` records so newly extracted datasets remain searchable as reference material. The GUI marks these states in edge details and warns that a route is not proof of safe or actual traversability.

- Rumor is excluded by default.
- Closed and Disappeared are excluded by default.
- False and Meta evidence are always excluded.
- Rejected edges are always excluded, even when NeedsReview inclusion is enabled.
- Deprecated nodes and their incident edges are excluded.
- AI route context uses the same edge policy as GUI/CLI route search.

The search UI can opt into Rumor and Closed/Disappeared. It cannot override the unconditional exclusions.

## Historical notes

Earlier revisions of this document described separate `*_v2.csv` outputs, strict default filtering, and a legacy fallback loader. Those statements describe superseded implementation history only. The current canonical filenames, validation rules, migration behavior, and filter defaults above take precedence.

Prior synthetic-fixture test counts and historical real-data migration counts are historical records, not current verification of this implementation. The repository's development instructions prohibit creating or running tests, mocks, fixtures, or dummy data; implementation changes must be manually checked by the developer against the real environment after explicit review.
