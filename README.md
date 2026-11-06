# Evidex

Evidex is a productivity and knowledge assistant for doctors. It assembles the context a doctor provides, retrieves relevant evidence from a curated library, and turns both into professional documents: referrals, case presentations, patient education leaflets, literature summaries and protocol drafts. Every claim can be traced to its source, and nothing is exported without the doctor's sign-off.

Built for **Health-a-thon 2026**: Diabetes track, Doctor Productivity & Knowledge Assistant. Team Synexis.

## The problem

Doctors act as the middleware between fragmented clinical information, external medical knowledge and the documents they have to produce. Typing is the cheap part. The expensive parts are:

- assembling context from scattered sources
- finding evidence and judging whether it applies
- adapting the same information for different audiences
- checking that nothing is wrong or missing

## How it works

```
Context  →  Evidence  →  Artifact  →  Verify  →  Approve
```

1. **Context:** case material and institutional templates supplied by the doctor are turned into traceable claims.
2. **Evidence:** a hybrid search over a curated library of Indian and international guidelines and open-access literature.
3. **Artifact:** the document is composed only from claims the doctor has approved, adapted to the audience.
4. **Verify:** checks that each reference exists, each quote matches its source, each claim is supported, and each number matches. Anything that fails is flagged rather than silently fixed.
5. **Approve:** the doctor reviews every claim and output. Export is locked until sign-off, and every change is audit-logged.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the agent pipeline.

## Scope

Evidex does not diagnose, recommend treatment for individual patients, score clinical risk, interpret medical data, or give autonomous clinical advice. See [docs/SCOPE.md](docs/SCOPE.md).

## Status (Round 2 MVP, in progress)

- **Done:** Layer 0, the **spine**: a diabetes knowledge graph anchored to the ADA Standards of Care in Diabetes 2026 (Sections 2 and 3). Postgres schema, fixed predicate list, CSV import with row-level validation errors, change log, tests.
- **Done:** spine read API (`get_assertions`, `outline_for`, `check_value`) and a Streamlit **encyclopedia**: concept search by name or synonym, every statement with its ADA anchor and review status, a graph of Sections 2 and 3, and a doctor edit form (grade, rec/table, page, checked flag, value) logged with the reviewer's name.
- **Next:** scope guard, planner, claim synthesis, number verifier, review screen, export.

The spine stores structured statements (concept, predicate, value, unit, population) and a reference to where each sits in the ADA document. It does not store ADA text. The spine content lives in the database, not in this repository.

## Stack

Python · PostgreSQL (Supabase) · Ollama (local models) · Streamlit · python-pptx / python-docx

## Spine quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                       # set DATABASE_URL
.venv/bin/python scripts/spine.py migrate  # create tables
.venv/bin/python scripts/spine.py check    # validate data/spine/*.csv, writes nothing
.venv/bin/python scripts/spine.py import --actor "Dr Vignesh"
.venv/bin/python scripts/spine.py stats
.venv/bin/python -m pytest -q
.venv/bin/streamlit run app.py             # encyclopedia at http://localhost:8501
```

After editing in the app, the database is the master copy; `scripts/spine.py export` writes it back to CSV.

CSV templates (headers only) are in `data/spine_templates/`. See [docs/SPINE.md](docs/SPINE.md).

## Repository layout

```
evidex/spine/   vocabularies and CSV importer for the spine
migrations/     Postgres schema
scripts/        command-line tools (spine.py)
data/           spine CSV templates (content itself is private)
tests/          pytest suite
eval/           evaluation harness and test sets (planned)
docs/           architecture, scope rules, spine guide
```
