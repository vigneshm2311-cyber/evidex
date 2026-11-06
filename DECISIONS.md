# Decisions log

| Date | Decision | Reason |
|------|----------|--------|
| 2026-10-02 | Represent content as structured claims, not prose | Makes verification, review and reuse across documents tractable |
| 2026-10-02 | Fixed LangGraph route rather than free-roaming agents | Predictable and auditable; no step can be skipped |
| 2026-10-02 | Deterministic code for reference, quote and number checks | These checks must not hallucinate |
| 2026-10-02 | Synthetic patient cases only in the MVP | Hackathon scope and data protection |
| 2026-11-06 | Spine first: one workflow over a curated ADA 2026 knowledge graph; the 13-step pipeline is the roadmap | Deadline; verification against structured values is the core claim |
| 2026-11-06 | Spine scope: ADA SoC 2026 Sections 2 and 3 (Section 1 skipped) | Sections 2 and 3 are threshold-heavy and fit the verifier; Section 1 is mostly care delivery |
| 2026-11-06 | PostgreSQL on Supabase free tier (Mumbai), replacing the SQLite MVP plan | Doctor's choice; reachable from any host; data kept out of the public repo |
| 2026-11-06 | Row-level security on, with no policies, on all spine tables | Supabase's REST API would otherwise expose the tables; the app connects as the DB owner |
| 2026-11-06 | Local LLM via Ollama on the doctor's Mac, served to evaluators through a tunnel; app in Docker, Ollama native | Free; Ollama inside Docker on macOS has no GPU access |
| 2026-11-06 | Seed values entered by Claude from long-standing ADA criteria, status `accepted` at the doctor's request; `doctor_checked=false` until he verifies; rec numbers, grades and pages left empty (TODO doctor) | Doctor's call to save time; the flag keeps it honest in the UI |
| 2026-11-06 | Three predicates added for Section 3: `prevented_or_delayed_by`, `has_target`, `requires_monitoring_of` | The original list could not express prevention statements |
| 2026-11-06 | One source anchor per assertion (`anc:<assertion id>`), filled from the same CSV row | One file for the doctor to fill; each statement has its own rec/page |
| 2026-11-06 | Spine CSVs gitignored; header-only templates committed | Repo is public; ADA licence and our own curation |
| 2026-11-06 | Values stored in conventional units only (%, mg/dL); no SI equivalents yet | Time; verifier can add unit conversion later |
| 2026-11-06 | Doctor edits happen in the app; the database is the master copy from then on, with `spine.py export` (and download buttons) writing it back to CSV | Faster than editing CSVs; export keeps a re-importable copy |
| 2026-11-06 | Anchor (rec/table, page) edits are logged in change_log by a trigger, like assertion edits | Full audit trail of who entered which reference |
| 2026-11-06 | Graph drawn with Streamlit's built-in Graphviz chart; unchecked statements drawn dashed | No extra dependency; review status visible at a glance |
| 2026-11-06 | Model: `qwen3:8b` via Ollama, thinking off, temperature 0, JSON-schema output | Correct on a 7-request scope set in ~3-7 s per call; llama3.1:8b was ~3x slower |
| 2026-11-06 | Agents get no tools or DB access; code selects the rows each sees; every call logged in `agent_call` | Least privilege and a complete audit trail |
| 2026-11-06 | Code rules can only make the Scope Guard stricter | A model mistake can cause a false refusal, never a patient-specific answer |
| 2026-11-06 | Dose/treatment/device topics answered "Not found in sources" by a code rule before any planning | Live test: the model answered a dosing question with prevention content; the spine has no such statements |
| 2026-11-06 | Planner gap list shown only when nothing could be planned | Live test: the model's "not covered" list was unreliable when it did produce an outline |
| 2026-11-06 | Number check: every number in a claim must exist in its cited rows; declared values must equal spine value and unit | Code, not a model, decides whether a number is right |
