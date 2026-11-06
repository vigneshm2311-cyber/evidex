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
- Agents (checkpoint 3): Ollama client with JSON-schema output, one retry and an `agent_call` audit table (migration 003); Scope Guard with code red flags; out-of-spine rule; topic lookup (`candidates_for`); Planner; Synthesizer; claim checks (numbers, units, IDs, patient wording, duplicates); fixed-route pipeline; `scripts/ask.py`.
- App: **Create** tab (topic, slides/handout, audience; step progress; decline with a suggested general question; Not found; claims with grade, IDs and source) and an audit view of what each agent saw.
- Live tests on qwen3:8b found and fixed: a false refusal of a population topic, patient details in the suggested question, filler outlines, a dosing question answered with prevention content, duplicate claims. 67 tests passing.
- Verifier and review (checkpoint 4): `draft` and `claim` tables with lock and change-log triggers (migration 004); Verifier with current-row checks, text-derived number/unit checks and an LLM support judge, one rewrite then flag; review functions (edit with re-verify, approve/reject, propose spine correction, sign-off); review screen and Drafts tab. Live check: an edit to "A1C 6.0–6.4 %" was flagged by code ("spine says 5.7–6.4 %") and approval was blocked; the LLM judge alone had missed it. 74 tests passing.
- Export (checkpoint 5): `evidex/export.py` (PPTX 16:9 and DOCX; title, sections with reference numbers, continuation slides, "Not provided", references with ADA anchors and unchecked-row notes, AI-use note, speaker notes with spine IDs and grades); fidelity checks; logged downloads. Live: a signed-off test draft exported to both formats and previewed. 81 tests passing.
