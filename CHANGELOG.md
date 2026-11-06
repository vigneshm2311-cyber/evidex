# Changelog

## 2026-11-06
- Narrowed scope: spine-first, one workflow end to end (see DECISIONS.md).
- Spine schema (`migrations/001_spine.sql`): concept, assertion, source_anchor, change_log; fixed predicate list enforced in the DB and in code; trigger checks (population type, required values); automatic change log with actor.
- CSV importer and CLI (`scripts/spine.py`: migrate, check, import, emit-sql, stats). Errors reported by file, line and column; nothing is written if any row fails.
- Seeded ADA 2026 Sections 2 (blocks A-G) and 3: 80 concepts, 93 assertions (79 Section 2, 14 Section 3). Rec numbers, grades and pages pending doctor entry; none doctor-checked yet.
- Supabase project `evidex` (ap-south-1) created with the schema and row-level security.
- 18 tests passing.
- Spine loaded into Supabase (80 concepts, 93 assertions, 93 change-log rows).
- Spine read API (`evidex/spine/api.py`): get_assertions, outline_for (teaching-order outline, empty when the spine has nothing), check_value (match / mismatch / not_in_spine, with expected values and assertion IDs).
- Streamlit encyclopedia (`app.py`): access code, concept search by synonym, statements with ADA anchors and TODO(doctor) markers, Section 2-3 graph, review-progress tab (lower-confidence rows first), doctor edit form with change history, CSV export.
- Migration 002: anchor edits logged. 36 tests passing, including a headless render of every tab.
