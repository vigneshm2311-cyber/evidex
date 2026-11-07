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
| 2026-11-06 | Support check ("NLI") done by the same local model as a judge, one call per section; it sees only the claim and its cited spine sentences | No new heavy dependency; isolation from the Synthesizer's reasoning |
| 2026-11-06 | Numbers are read from the claim text by code (not from what the model says it used) | Same check for model drafts and doctor edits; live test showed the LLM judge missing a wrong number |
| 2026-11-06 | Flagged claims cannot be approved; numbers need a "checked" tick; sign-off needs every claim decided and locks the draft (DB trigger) | No export of anything unreviewed |
| 2026-11-06 | Unchecked spine rows behind approved claims produce a warning at sign-off, not a block | Doctor's choice; blocking would require checking all 93 rows first |
| 2026-11-06 | A disputed number becomes a `proposed` spine row; the spine changes only when the doctor accepts it | "No AI agent writes to the spine"; edits flow back as proposals |
| 2026-11-06 | Export built by code from the exact approved text; no AI Composer after sign-off | The signed text is what ships; an AI Composer would need its own review step (roadmap) |
| 2026-11-06 | Export fidelity checked twice: on the document model and on the text read back from the rendered file | Catches layout code that drops or alters a claim |
| 2026-11-06 | Exports logged in change_log with SHA-256 and claim IDs | Ties each file to its sign-off |
| 2026-11-06 | Public access via ngrok free static domain to the app on the doctor's Mac; app bound to 127.0.0.1, Ollama and DB not exposed | Stable link without a paid host; local GPU for the model |
| 2026-11-06 | Two codes: evaluator (create, review, sign off, export) and doctor (also edit the spine) | Evaluators can try every feature without changing the spine |
| 2026-11-06 | One model job at a time (shared lock) and 10 drafts per hour per visitor | A local 8B model cannot serve parallel runs; protects the demo from overload |
| 2026-11-06 | Access code kept out of the public repo; given only in the submission | The repository is public |
| 2026-11-06 | Evaluation questions drafted by Claude, gold labels only from the doctor; unlabelled metrics reported as "not computed" | Brief: never generate gold answers or results |
| 2026-11-06 | Number-error injection test (code alters one number per passing claim; measures code Verifier vs LLM judge) | Needs no clinical gold: the spine is the reference |
| 2026-11-06 | Evaluation runs are not saved as drafts; their agent calls are logged with an `eval-` run prefix | Keeps the Drafts list clean, keeps the audit trail |
| 2026-11-07 | Drafts run in a background thread with an in-memory job registry; job id in the URL | A rerun or reconnect stopped in-page runs and lost minutes of work; no extra dependency (no queue server) for one local model |
| 2026-11-07 | Wording faults (cut-off word, run-together words, repeated phrase) are Verifier issues and block approval until edited | Both the model and a doctor's edit produced such text and it passed as ✔; checks are narrow to avoid false flags (1/172 on eval claims, a true one) |
| 2026-11-07 | Rows the Planner chose but no claim cites are a warning, not a block | The doctor decides whether the slides need them |
| 2026-11-07 | Online version: Streamlit Community Cloud + Groq (`LLM_PROVIDER=groq`); local Ollama stays the default | Runs without the doctor's laptop, faster drafts; the doctor chose this route |
| 2026-11-07 | Hosted model `qwen/qwen3.8-27b` (Qwen3-32B no longer offered on Groq); JSON mode with the schema in the prompt and checked in code | Same model family as the local qwen3:8b; Groq does not enforce our JSON schema for it |
| 2026-11-07 | Local-model evaluation kept as `eval/REPORT_local_qwen3-8b.md`; `eval/REPORT.md` holds the hosted-model run | Each published number must match the model actually serving |
| 2026-11-07 | Three-phase product (Request → Building → Draft) replaces the tab-first UI; old screens move to Library | Doctor's call: end-user language and time to value, not the app's internals |
| 2026-11-07 | Literature layer: PubMed E-utilities (abstracts, no key) plus uploaded PDF/PPTX, stored in `source_doc`; cited as `pmid:<n>` / `up:<hash>` alongside spine IDs | Spine stays the grounding layer; sources add context; one claim model and one Verifier for both |
| 2026-11-07 | Literature numbers must appear verbatim in the cited source text (code check); support judge sees the cited abstract or passage | Same "code decides numbers" rule as for the spine |
| 2026-11-07 | Four personas (students, residents/CME, nurses/educators, patients/public) and four formats (slides, handout, flowchart, one-page PDF) | Doctor's spec; flowchart and one-pager are drawn by code from approved claims only |
| 2026-11-07 | `pypdf` (read PDFs) and `fpdf2` (one-page PDF), DejaVu Sans bundled for Unicode symbols | Pure Python, small; approved by the doctor |
| 2026-11-07 | Two approvals: the user confirms Evidex's understanding before anything is built, then approves the finished preview; claim-level review stays under "Detailed review" | Doctor's spec: outcome first, detail on request |
| 2026-11-07 | Backup models on Groq's daily cap (`LLM_FALLBACKS`: gpt-oss-120b, then gpt-oss-20b); fail fast on a daily cap instead of waiting | Free tier = 200k tokens/day per model (~8-15 drafts); doctor chose free backups over the paid tier. Backups are not formally evaluated; code checks still apply and the export note names the model used |
| 2026-11-07 | Support judge gets each source once (claims name their source IDs); shorter passages and planner snippets | Cut tokens per draft: the judge was ~45% of a draft's tokens because an abstract was resent for every claim citing it |
