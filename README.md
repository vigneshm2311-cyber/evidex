# Evidex

**Verified diabetes teaching material in minutes.** A doctor types a sentence (or attaches papers or a deck), picks who it is for and the format, and Evidex builds slides, a handout, a flowchart or a one-page summary, grounded in a doctor-curated knowledge base from the *ADA Standards of Care in Diabetes 2026* and recent PubMed research. Code checks every number against its cited source, and nothing is downloaded until a doctor approves it.

Built for **Health-a-thon 2026**: Diabetes track, Doctor Productivity & Knowledge Assistant. Team Synexis (AZYZ69): Dr Vignesh M, MBBS, and Mughil.

- **Live app:** https://evidex-synexis.streamlit.app (access code in the submission form; steps in [docs/ACCESS.md](docs/ACCESS.md))
- **Demo video:** https://drive.google.com/file/d/1Jach-O1yG04XrHin774niluvRJNw7RR_/view?usp=sharing
- **Evaluation:** [eval/REPORT.md](eval/REPORT.md) · **Product rules:** [docs/PRODUCT_RULES.md](docs/PRODUCT_RULES.md) · **Scope rules:** [docs/SCOPE.md](docs/SCOPE.md)

## The problem

Doctors who teach spend their time finding, adapting and re-checking guideline numbers, not typing. Generic AI makes checking harder: it drafts fast but invents or misquotes sources.

## How it works: three phases

```
1 · Your request   →   2 · Building   →   3 · Your draft
```

1. **Your request.** A sentence, PDF papers or a PPTX deck; persona (medical students, residents/CME, nurses and educators, patients and public); format (slides, handout, flowchart, one-page PDF). A patient-record check runs on text and files; patient-specific requests are declined. Evidex says what it understood (title, topic, key questions) and **nothing is built until the user confirms**.
2. **Building.** The content is grounded in the **spine** (80 concepts, 93 structured ADA 2026 statements, Sections 2–3: diagnosis, classification, screening, prevention). Recent **PubMed** abstracts (records only from the API) and passages from the uploads add context. Statements are written for the persona; **code checks every number** (guideline values against the spine, study numbers against the cited abstract) and an AI judge checks support; a failing statement is rewritten once, then flagged.
3. **Your draft.** A preview in the chosen format; edit (re-checked) or remove any statement; approve; download a file containing exactly the approved text, with numbered references and an audit log.

A topic the spine does not cover gets **"Not found in sources"** plus the nearest topics Evidex can make. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Scope

A base prototype focused on functionality: general teaching material only. Evidex does not diagnose, recommend treatment for individual patients, score clinical risk or interpret a patient's data. See [docs/SCOPE.md](docs/SCOPE.md).

## Free-tier note

The hosted prototype runs on Groq's free tier: about 8,000 tokens a minute and 200,000 a day per model (roughly 8–15 drafts a day, shared). A busy minute makes a draft slower; when the main model's daily cap is reached Evidex switches to backup models (`LLM_FALLBACKS`). A paid tier removes both limits. See [docs/DEPLOY.md](docs/DEPLOY.md).

## Build history (Round 2)

- **Done:** Layer 0, the **spine**: a diabetes knowledge graph anchored to the ADA Standards of Care in Diabetes 2026 (Sections 2 and 3). Postgres schema, fixed predicate list, CSV import with row-level validation errors, change log, tests.
- **Done:** spine read API (`get_assertions`, `outline_for`, `check_value`) and a Streamlit **encyclopedia**: concept search by name or synonym, every statement with its ADA anchor and review status, a graph of Sections 2 and 3, and a doctor edit form (grade, rec/table, page, checked flag, value) logged with the reviewer's name.
- **Done:** agents on a local model (Ollama): Scope Guard, Planner and Synthesizer on a fixed route, with code checks on every output and a full audit log. The app's **Create** tab declines patient-specific requests, answers "Not found in sources" when the spine has nothing, and drafts claims that each cite spine assertions with numbers checked. See [docs/AGENTS.md](docs/AGENTS.md).
- **Done:** Verifier (code checks on spine rows, numbers and units, plus an LLM support judge; one rewrite, then flag) and the doctor **review screen**: edit (re-verified), approve/reject, click-to-source, propose a spine correction, sign-off that locks the draft. Drafts and decisions are stored and logged.
- **Done:** **export** after sign-off: PPTX slides or DOCX handout built by code (no model after sign-off) from exactly the approved claim text, with numbered references to spine rows and ADA anchors, "Not provided" for fully rejected sections, and an AI-use note. A fidelity check blocks any mismatch; every export is logged with a SHA-256.
- **Done:** **online hosting** at https://evidex-synexis.streamlit.app (Streamlit Community Cloud + Supabase + Qwen3.8-27B on Groq, `LLM_PROVIDER=groq`; see [docs/DEPLOY.md](docs/DEPLOY.md)): evaluator and doctor codes, one model job at a time, per-visitor limit, in-app "How to test". The local-model setup (Ollama, ngrok via `scripts/serve.sh`) still works as a backup. See [docs/ACCESS.md](docs/ACCESS.md).
- **Done:** **v2, three phases** (`evidex/studio.py`, `evidex/sources.py`): request → understood → build → draft; PDF/PPTX upload with a patient-record check; PubMed literature layer; four personas and four formats (flowchart PPTX and one-page PDF drawn by code); suggestions on "Not found"; backup models on the free tier's daily cap; live progress screens.
- **Done:** **evaluation harness** (`scripts/eval.py`, see [eval/README.md](eval/README.md)): automatic metrics against the spine, a number-error injection test, gold-labelled scope and "Not found" metrics from the doctor's labels; results in `eval/REPORT.md`.

The spine stores structured statements (concept, predicate, value, unit, population) and a reference to where each sits in the ADA document. It does not store ADA text. The spine content lives in the database, not in this repository.

## Stack

Python 3.11 · Streamlit (Community Cloud) · PostgreSQL (Supabase, Mumbai) · Groq: Qwen3.8-27B, backups gpt-oss-120b / 20b (or Ollama `qwen3:8b` locally) · PubMed E-utilities · pypdf · python-pptx / python-docx / fpdf2

## Spine quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # set DATABASE_URL
.venv/bin/python scripts/spine.py migrate  # create tables
.venv/bin/python scripts/spine.py check    # validate data/spine/*.csv, writes nothing
.venv/bin/python scripts/spine.py import --actor "Dr Vignesh"
.venv/bin/python scripts/spine.py stats
.venv/bin/python -m pytest -q
.venv/bin/streamlit run app.py             # app at http://localhost:8501 (needs `ollama serve`)
.venv/bin/python scripts/ask.py "Diagnosing diabetes in Indian adults"   # one run in the terminal
```

After editing in the app, the database is the master copy; `scripts/spine.py export` writes it back to CSV.

CSV templates (headers only) are in `data/spine_templates/`. See [docs/SPINE.md](docs/SPINE.md).

## Repository layout

```
evidex/         pipeline (studio.py: three phases; pipeline.py: spine-only route), agents, sources, export
evidex/spine/   spine API, vocabularies and CSV importer
migrations/     Postgres schema
scripts/        command-line tools (spine.py)
data/           spine CSV templates (content itself is private)
tests/          pytest suite
eval/           evaluation harness, questions and reports
docs/           architecture, scope rules, spine guide
```
