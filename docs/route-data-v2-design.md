# Route database v2 – implementation notes

## Scope and safety

- The current v1-style files are read-only inputs during the redesign.
- Do not overwrite `backrooms_nodes.csv` or `backrooms_edges.csv` during schema development.
- The v2 files are separate outputs: `backrooms_nodes_v2.csv`, `backrooms_edges_v2.csv`, and `backrooms_evidence_v2.csv`.
- No migration should be considered authoritative until validated against the original full dataset and a reviewed article corpus.

## Data model

### Nodes

`Node_ID, Canonical_Title, Canonical_URL, Aliases, Comprehension, Danger, Node_Type, Data_Status`

A node represents one canonical location. Preserve source titles, URLs, and aliases. URL-derived IDs are preferred when a canonical URL is known; never merge ambiguous title matches automatically.

### Edges

`Edge_ID, Source_ID, Target_ID, Direction, Certainty, Availability, Evidence_Type, Probability, Condition, Review_Status`

An edge represents a route relation between canonical nodes. Certainty, availability, evidence type, and probability are independent dimensions. Unknown is a valid explicit value; do not infer probability from certainty.

### Evidence

`Evidence_ID, Edge_ID, Article_ID, Relation_Type, Source_Raw, Target_Raw, Raw_Text, Extract_Method, Confidence, Certainty, Availability, Evidence_Type, Probability`

Evidence is article-specific and many evidence rows may support one edge. Keep raw text and raw endpoint labels for auditability. Evidence-level classifications may differ and must not be silently collapsed.

## Direction semantics

`Relation_Type` describes the article section, not the direction itself.

- In an article for Level 8, an Entrance statement saying entry from Level 9 into Level 8 is stored as Source=Level 9, Target=Level 8, Relation_Type=Entrance.
- An Exit statement in the Level 8 article leading to Level 9 is stored as Source=Level 8, Target=Level 9, Relation_Type=Exit.

## Controlled values

- Direction: Directed / Bidirectional / Unknown
- Certainty: Confirmed / Uncertain / Unknown
- Availability: Available / Closed / Disappeared / Unknown
- Evidence_Type: Actual / Rumor / False / Meta / Unknown
- Probability: Common / Uncommon / Rare / ExtremelyRare / Unknown
- Review_Status: Reviewed / NeedsReview / Rejected
- Relation_Type: Entrance / Exit

Legacy values such as Unspecified must be mapped to Unknown only in an explicitly reviewed migration, not by mutating source files.

## Route search policy (planned)

Default results should include only Confirmed + Available edges, excluding False and Meta evidence. Separate opt-ins will control uncertain, rumor, closed/disappeared, rare, and NeedsReview records. The search UI must show the article evidence and conditions behind each selected edge.

## Phases

1. Define and test the v2 schema (current phase).
2. Build a non-destructive migration/extraction pipeline with conservative node resolution.
3. Add route search filters, weights, and evidence presentation.
4. Validate extraction against a manually reviewed real-article regression corpus.

## Current investigation notes (2026-11-19)

- Project working directory: `F:\Projects\BrNavi-FandomJP-GUI`.
- Existing worktree contains many pre-existing modified and untracked files; none were reverted.
- Current root CSVs contain 93 nodes and 126 edges. They do not match the previously analyzed full dataset (1,332 nodes / 7,175 edges), so they are not treated as the authoritative v1.0.1 baseline.
- Full test suite at investigation start: 177 passed, 1 skipped, 1 warning, 38 subtests passed.
- Route-specific tests: 8 passed.
- The current extractor still writes directly to the legacy filenames and uses section-wide fallback inference. That behavior must be isolated before v2 output is enabled.

## Migration implementation

`modules/route_migration.py` provides an explicit, non-destructive legacy import:
- Reads the old node and edge CSV formats.
- Writes only the three `*_v2.csv` files into a caller-specified output directory.
- Preserves each old edge row as an individual evidence row while grouping edges by source, target, and direction.
- Uses deterministic legacy IDs when canonical URLs are not present; these are placeholders, not claims of canonical identity.
- Leaves certainty, availability, evidence type, probability, and confidence unknown and marks imported nodes/edges for review.
- Keeps original raw endpoint labels and condition text.

## Progress update (2026-11-19)

- Added `modules/route_schema.py`: explicit v2 column contracts, enum constraints, row validation, and CSV validation.
- Added `modules/route_migration.py`: non-destructive conversion into a separate output directory. Every legacy row remains an evidence record; repeated endpoint pairs share an edge while retaining separate evidence. Existing Confirmed/Uncertain values and legacy extraction-method labels are preserved when present. Conflicting certainty across evidence is downgraded to Unknown at the aggregate edge level.
- Added schema and migration tests. Route-focused suite: 20 passed. Full suite: 189 passed, 1 skipped, 1 warning, 38 subtests passed.
- The migration has only been exercised against synthetic fixtures. It has NOT been run against the project CSVs or the previously analyzed 1,332/7,175 source dataset.
- Existing legacy CSV files remain untouched. GUI and route search still use the existing legacy data path; v2 integration is not enabled.
- `git diff --check` reports trailing-whitespace diagnostics in several pre-existing modified files (`modules/database.py`, `modules/downloader.py`, `modules/image_downloader.py`, among others); these were not mass-formatted because they are outside this task and contain unrelated user changes.

## GUI integration and real-data smoke test (2026-11-19)

### GUI / service integration
- `modules/route_service.py` now prefers a complete v2 CSV set and falls back to legacy CSVs when v2 is absent.
- V2 loading preserves canonical node IDs, aliases, URLs, edge dimensions, and per-article evidence rows.
- Route filtering is independent: uncertain, unknown, rumor, closed/disappeared, and NeedsReview records each require explicit opt-in. False and Meta evidence are always excluded.
- Legacy graph records retain their former permissive behavior so a project without v2 files keeps working.
- The GUI route tab exposes the five opt-in filters and passes them to the Python API.
- Route detail cards now display the four status dimensions, review status, and attached article evidence.
- The GUI Python API accepts an optional filter dictionary and forwards only the supported boolean options.
- The AI assistant continues to use the shared route service. Its route context can include unknown/uncertain evidence as reference context, not as a confirmed travel claim.

### Real-data migration
- Ran the non-destructive migration against the current project root CSVs.
- Source files `backrooms_nodes.csv` (93 rows) and `backrooms_edges.csv` (126 rows) were not overwritten.
- Generated:
  - `backrooms_nodes_v2.csv`: 93 rows
  - `backrooms_edges_v2.csv`: 121 unique directed edges
  - `backrooms_evidence_v2.csv`: 126 article evidence rows
- 5 repeated source-target rows were retained as separate evidence rows while their shared edges were grouped.
- All migrated rows remain NeedsReview with unknown certainty/availability/evidence type/probability where the legacy source had no corresponding values.
- The default strict search correctly returns no route for Level 0 → Level 4 because the imported records are not yet reviewed.
- With Unknown + NeedsReview enabled, real-data smoke test found a 3-edge route: Level 0 → Level 1 → Level 2 → Level 4.

### Verification
- Route-related tests: 25 passed.
- Full test suite: 194 passed, 1 skipped, 1 warning, 38 subtests passed.
- `py_compile` succeeded for `gui_app.py` and the route schema/migration/service modules.
- Started the updated GUI with Pythonw; process PID 11184 was responding after startup.
- A visual click-through of the desktop UI has not yet been confirmed; backend integration and GUI process startup are verified, but user-side visual verification remains outstanding.

- GUI process tree check: launcher PID 11184 spawned the visible pywebview window process PID 2040; its window handle is nonzero and title is "Backrooms Navigation System - Fandom JP v1.0.0 (Wiki Explorer)". An extra console-launched test instance was stopped; the primary GUI window remains running.
- Actual route smoke test confirmed the UI-facing response shape: the 3 transitions expose edge-info keys using the displayed canonical aliases (Level 0 / Level 1 / Level 2 / Level 4), with one article evidence row attached to the first transition.
