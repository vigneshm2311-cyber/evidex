# Evidex: full project context for Claude Code

Paste this whole file into Claude Code at the start of the session, or save it in the repo as `docs/CONTEXT.md` and point Claude Code at it. It summarises every decision made so far. Read it fully before planning anything.

---

## 0. How to work with me (read first)

- I am Dr. Vignesh M (MBBS), the doctor on Team Synexis. I will give feedback; you do the building.
- **Plan first.** Before writing code, give me a short plan with checkpoints and wait for my OK.
- **Work in small, demonstrable steps.** After each step, show me what works (a command to run, a screen to open, test output) and ask for feedback before the next step.
- **Never invent clinical content.** No recommendation numbers, evidence grades, thresholds, citations, PMIDs, DOIs, evaluation results or user-testing findings that did not come from me or from a real source/API response. Where something is missing, leave a clearly marked `TODO(doctor)` and tell me.
- **Ask before** adding major dependencies, changing the scope rules (section 3) or anything that costs money.
- Keep a `DECISIONS.md` log of architecture choices, and a `CHANGELOG.md` of what was built each day (I need this for the "Build Sprint journey" slide).
- Never commit secrets. Keys go in `.env`; keep `.env.example` updated.

---

## 1. The situation

- **Event:** Health-a-thon 2026 (India's doctor × technologist hackathon for assistive AI). Track: **Diabetes · Doctor / Care Team · Doctor Productivity & Knowledge Assistant**.
- **Team:** Synexis, team code **AZYZ69**. (Team member names and roles: `TODO(doctor)`.)
- **Round 1:** idea submitted (Evidex, building on my earlier NEXUS project).
- **Round 2 deadline: 7 November 2026.** Four deliverables (section 9). As of 6 November, **nothing of Evidex is built yet**; the design is settled. Time is the main constraint, so scope must stay small (section 7).
- **Repos (GitHub account `vigneshm2311-cyber`):**
  - `evidex`: scaffold only (README, CLAUDE.md, DECISIONS.md, .gitignore, .env.example, docs/SCOPE.md, docs/ARCHITECTURE.md). `docs/BUILD_PROMPT.md` was never uploaded. This file supersedes it.
  - `nexus`: my earlier working multi-agent research platform (section 6). Reuse from it.

---

## 2. What Evidex is (the thesis)

Doctors act as the middleware between fragmented clinical information, external medical knowledge and the professional documents they must produce (teaching slides, patient education, literature summaries, protocols, referrals). Typing is cheap; the expensive parts are assembling context, finding and judging evidence, adapting it for different audiences, and **verifying** it. Generic AI drafts quickly but invents references and defaults to Western practice, so doctors must re-check everything and save no time.

**Evidex** is a productivity and knowledge assistant built on a verified knowledge spine:

```
Context → Evidence → Artifact → Verify → Approve
```

- Every claim traces to a source; nothing is exported without the doctor's sign-off.
- If the sources don't contain the answer, it says **"Not found in sources"** instead of guessing.
- Metric that matters: **time to a signed-off document**, not time to a first draft.
- UI is **artifact-oriented** ("What are you creating?"), not a chat box.

---

## 3. Hard scope rules (non-negotiable)

The hackathon excludes: diagnosis, treatment recommendations, clinical decision support, clinical risk scoring, interpretation of medical data, autonomous clinical advice. Therefore:

1. **Evidence is general, never patient-specific.** Evidex reports what guidelines say. It never states what a particular patient should receive and never judges whether evidence applies to a patient.
2. **Patient facts come only from the doctor's own words** (case tasks, later phase): every patient fact must trace to an exact text span the doctor wrote. Extract and restructure only; never infer or interpret.
3. **The spine is an encyclopedia, not a decision engine.** It answers "what does ADA say about X, and where?" It is never traversed to produce "what this patient should get".
4. **Missing information shows as "Not provided"**; never filled in.
5. **No real patient data.** Synthetic cases only.
6. **No autonomous action.** Nothing is sent or published; export unlocks only after sign-off.
7. **No answer without sources; no invented references.**

If a request conflicts with these, stop and tell me.

---

## 4. Layer 0: the Spine (BUILD THIS FIRST)

### 4.1 Idea
A **doctor-curated diabetes knowledge graph**, anchored to the **ADA Standards of Care in Diabetes 2026** (Diabetes Care, vol 49, Supplement 1) as the **cited authority**. It is the doctor's encyclopedia first; every other layer reads from it; additions are proposed on top and reviewed before they count.

### 4.2 Licensing constraint (important)
ADA's copyright notice allows educational, noncommercial use if cited and unaltered, but requires permission to post on third-party sites and **prohibits text/data mining without permission**. My decision: this is a **private proof of concept**, ADA is used as a **cited authority**, and I **curate entries by hand** from my own copy.
So:
- **Do not write scrapers or LLM pipelines that bulk-extract ADA text.**
- Store my structured entries (concepts, thresholds, relations) plus **references** (section, rec number, table, page), not ADA paragraphs.
- Do not publish ADA text anywhere public.
- Quotable text for verification can come from **openly licensed** sources (open-access CC-BY papers, WHO, etc.), added later.

### 4.3 Data model: statements, not just links
A plain edge cannot hold threshold, unit, population, condition and grade, so each guideline statement is its own record (**assertion**) joining two concepts.

Tables (SQLite for the MVP; Postgres later is fine):

**concept**
| field | example |
|---|---|
| id | `test:a1c` |
| type | Condition / Test / Threshold / Population / RiskFactor / Intervention / DrugClass / Complication / Outcome / Rule |
| name | Glycated haemoglobin (A1C) |
| synonyms | HbA1c, glycosylated haemoglobin |
| codes (optional) | LOINC / SNOMED / ICD (leave empty for MVP) |

**assertion**
| field | example |
|---|---|
| id | `ada26-s2-dx-a1c` |
| subject_id | `cond:diabetes` |
| predicate | `diagnosed_by` |
| object_id | `test:a1c` |
| value / unit / operator | `6.5` / `%` / `>=` (or a range low–high) |
| population_id | `pop:adults_nonpregnant` |
| condition_text | e.g. "NGSP-certified, DCCT-standardised lab method" |
| grade | from the document, `TODO(doctor)` until entered |
| status | draft / proposed / accepted / rejected / superseded |
| source_anchor_id | → source_anchor |
| added_by, reviewed_by | names |

**source_anchor**: `doc` (ADA SoC), `edition` (2026), `section` (2), `locator` (Rec 2.x / Table 2.x), `page`, optional `url`.

**change_log**: record id, action (e.g. proposed→accepted), by, at, before/after.

**Fixed predicate list:** `classified_as`, `subtype_of`, `diagnosed_by`, `has_threshold`, `requires_confirmation`, `unreliable_when`, `use_instead`, `screen_with`, `screening_starts_at`, `repeat_interval`, `risk_factor_for`, `defined_by`. Validate against this list in code.

**Lifecycle:** doctor entry → `draft` → `accepted`; suggestion/overlay → `proposed` → `accepted` (named reviewer) or `rejected`; new edition → `superseded` (kept, with edition). **No AI agent writes to the spine directly**; AI suggestions enter as `proposed`.

**Overlays (later):** RSSDI / ICMR assertions attach to the same concepts with their own anchors; comparing assertions with the same subject + predicate + population across sources = deviation analysis.

### 4.4 First content: ADA 2026 Section 2, Diagnosis and Classification
I will enter values, rec numbers and grades from my copy. Build the structure and an entry workflow; pre-list the items as empty rows.

| Block | Content | Priority |
|---|---|---|
| A. Diagnostic criteria | A1C, FPG, 2-h 75 g OGTT, random glucose with symptoms; confirmation rule | Must |
| B. Prediabetes | A1C range, IFG, IGT | Must |
| C. When A1C is unreliable | haemoglobinopathies; altered red-cell turnover (pregnancy, dialysis, blood loss/transfusion, EPO, G6PD) → use glucose criteria | Must (India angle) |
| D. Screening adults without symptoms | overweight + risk factors (lower BMI cut-off for Asian ancestry), all adults from a set age, repeat intervals | Must (India angle) |
| E. Classification | T1D, T2D, specific types (monogenic, pancreatic, CF-related, drug-induced, post-transplant), GDM | Should |
| F. Type 1 staging | stages 1–3, autoantibodies | Should |
| G–H. Children; special types | | Later |

Target for the demo: blocks A–D, roughly 30–40 concepts and 30–40 assertions. (Reference values in our discussion were the long-standing ADA criteria, e.g. A1C ≥ 6.5 %, FPG ≥ 126 mg/dL, 2-h ≥ 200 mg/dL, prediabetes A1C 5.7–6.4 %, IFG 100–125, IGT 140–199. **Do not seed these as accepted data.** I confirm each against the 2026 text.)

### 4.5 Spine deliverables
1. Schema + migrations + predicate validation.
2. An easy entry path for me: a CSV/Google-Sheet-style template per table that imports into the DB with validation errors shown clearly (fastest for me), and/or simple forms.
3. **Encyclopedia view:** concept page listing its assertions (value, unit, population, grade, status) and the anchor for each; a small graph view of Section 2.
4. A read API used by the agents: `get_assertions(concept, predicate=None, population=None, status="accepted")`, `outline_for(topic)`, `check_value(concept, predicate, value, unit)`.

---

## 5. The agent pipeline (layers on top of the spine)

Fixed route (LangGraph or a plain Python state machine; no agent chooses its own route). Typed JSON between steps. **Agents handle judgement; code handles anything checkable.** Each agent sees only the context it needs.

| # | Step | Kind | Job | Sees / never sees | Uses spine |
|---|---|---|---|---|---|
| 0 | Librarian | code + doctor | Ingest open-access text into page-anchored chunks tagged with spine concepts | one document + metadata | concept tagging |
| 1 | Scope Guard | agent (fast, temp 0) | Allow in-scope requests; decline patient-specific advice and offer a general question | request only; never the library | – |
| 2 | Context Assembler | agent | (Case tasks, later) doctor's notes → facts with exact spans | notes only; never the library | – |
| 3 | Task Planner | agent | Outline + information needs + Indian and international queries | request, template, profile; never chunk text | outline from accepted assertions |
| 4 | Evidence Retriever | code | Hybrid search + rerank, top-k per need | queries | filter by concept tags |
| 5 | Sufficiency Judge | agent (temp 0) | Enough evidence per need? If not: "Not found in sources" | one need + its chunks | – |
| 6 | Claim Synthesizer | agent (strongest) | Atomic neutral claims, each with verbatim quote + source; link to assertion ID | sufficient chunks/assertions; never patient facts | claims link to assertions; grade inherited from spine, not model |
| 7 | Deviation Analyst | agent | Indian vs international differences, both sides cited | claims + quotes | compare overlay assertions (later) |
| 8 | Verifier | code + NLI | Reference exists; quote verbatim in source; numbers match; not superseded; claim entailed. One retry, then flag | one claim + its source; never the writer's reasoning | **numbers checked against assertion values** |
| 9 | Doctor review: claims | human | Approve / edit / reject each claim; tick numbers | everything | edits → `proposed` assertions |
| 10 | Artifact Composers | agent | Restyle approved claims into slides / handout / leaflet; every sentence lists claim IDs; "Not provided" for gaps | approved claims only; never raw sources | – |
| 11 | Fidelity Checker | code + NLI | Every sentence maps to approved claims; no new numbers; sections present; readability | draft + approved claims | – |
| 12 | Doctor review: output | human | Final look, click-to-source; sign-off unlocks export | everything | – |
| 13 | Export + audit log | code | PPTX/DOCX/PDF + AI-use note; log every agent output and doctor edit | approved content | – |

Draft system prompts (adapt):
- **Scope Guard:** classify as `in_scope_topic` / `in_scope_case` / `out_of_scope`; out of scope = diagnosis, what a specific patient should take, dosing for a patient, risk scores, interpreting a patient's results. Return JSON with reason and a neutral `general_query`.
- **Planner:** "Plan a {artifact} for {audience} on {topic}. Use these accepted spine assertions as the backbone: {assertions}. For each section list needs, mark evidence / context / doctor_must_provide. Do not answer any need."
- **Synthesizer:** "Using ONLY these sources/assertions, write one-fact claims, each with the exact supporting quote, the source id and the assertion id. Never use outside knowledge. Never say what a particular patient should do. Return JSON."
- **Composer:** "Build {n} slides for {audience} using ONLY these approved claims. Every bullet lists its claim IDs. Add no facts, numbers or recommendations. Empty sections say 'Not provided'."

---

## 6. Reuse from NEXUS (github.com/vigneshm2311-cyber/nexus)

NEXUS: Flask app, 7 agents (Genesis, Fetcher, Critic, Ranker, Evolver, Analogy Bridge, Gap Detector), live search across PubMed, Europe PMC, OpenAlex, ClinicalTrials.gov, bioRxiv, medRxiv (`nexus/sources/*.py`, `nexus/agents/fetcher.py`), paper-link builder (`nexus/paper_links.py`), SQLite storage, local Ollama models.
- **Reuse:** source clients (PubMed, Europe PMC), query utilities, paper links, LLM retry wrapper pattern.
- **Change:** Evidex does not generate hypotheses; it reports only what sources say, with code-based verification and doctor sign-off.
- A patch exists (not yet pushed) adding a hosted demo mode to NEXUS: fixed small model (`qwen2.5:1.5b`), capped run size, one run at a time, and a **security fix restricting `/download` to the reports folder** (the original allowed arbitrary file reads). If NEXUS is ever exposed publicly, apply that fix first.

---

## 7. MVP scope for Round 2 (keep it small)

One workflow end to end, done well:

> Doctor picks a topic within Section 2 (e.g. "diagnosing diabetes in Indian adults") and an output (slides or teaching handout) → planner builds the outline from the spine → claims cite spine assertions (and open-access text where available) → verifier checks numbers against the spine → doctor approves on one review screen with click-to-source → export PPTX/DOCX.

Must show in the demo:
- the spine encyclopedia view (Section 2 A–D),
- click any sentence → its ADA anchor,
- **"Not found in sources"** for a question the spine/library can't answer,
- **Scope Guard declining** a patient-specific question ("what dose should I start for this patient?"),
- a number mismatch caught by the verifier.

Out of scope for now (roadmap): referrals/patient context, Tamil/Hindi output, RSSDI/ICMR overlays, two-stage review, preference learning, full 13-step pipeline.

**Stack suggestion:** Python; SQLite; Streamlit for the UI (fastest path) or FastAPI + a simple frontend; Anthropic API for agents (model name in `.env`); python-pptx / python-docx for export. Ask me before choosing otherwise.

**Hosting:** evaluators must be able to open and test it, with access instructions/credentials. Hugging Face Docker Spaces now need a paid PRO plan. Options: Streamlit Community Cloud (free, from a public GitHub repo, API key in secrets) — preferred; or run on my laptop with a tunnel (ngrok free static domain or Cloudflare). Add a simple access code. Test the link from another device before submitting.

---

## 8. Evaluation (must produce real numbers)

- Create `eval/questions.jsonl` with the schema and ~5 example rows; **I write the gold answers** (target ~30 questions, including ~5 the spine cannot answer). Do not generate gold answers.
- Metrics: citation/anchor existence, number accuracy vs spine, correct "Not found", scope-guard block rate and false-refusal rate, fidelity (sentences mapped to approved claims).
- Report honestly; never hard-code results. If something wasn't run, say so.
- User validation: I will get 3–5 doctors to try it; record their feedback in `eval/user_feedback.md` (only what they actually said).

---

## 9. Round 2 deliverables (due 7 Nov 2026)

1. **Functional MVP** with link, credentials and access instructions.
2. **Two-page overview** on the prescribed template (Two-page_Solution_Brief_Template.pptx). Sections: About the team; Doctor background and role; Problem statement (use case, evidence, users, context); AI solution description; Technology and tools; Key features (3–5); Build sprint progress; User validation and AI evaluation; Safety and responsible AI; Deployment and sustainability; Supporting links (MVP, video, others).
3. **Final presentation** on the prescribed template (PPT_Final_Teamplate_Health-a-thon_2026.pptx), 12 slides: Title (solution name, subtitle, team name, team code) · Team overview & background · Problem statement & evidence · Target users & use case · Solution overview, features & impact (max 2 slides) · Technology, tools & AI approach (simple flow diagram) · Build sprint journey & progress (timeline; what is functional; how it evolved from Round 1; challenges; what remains) · Evaluation & user validation (up to 2 slides) · Safety & responsible AI · Deployment, cost & sustainability (pilot needs, integrations, one-time cost, cost per transaction and per doctor/month with assumptions) · What next (+ "make solution info public? Yes/No") · Thank you (team name, code, contact email, MVP link, demo video link, code repo link).
4. **Narrated demo walkthrough video**: introduce the team, how we approached the problem, collaboration with doctor and tech partners, demo of the working solution and key features. Narrated, not just a screen recording.

I will place both template files in `docs/templates/`. When filling them, keep the template layout, fill only real content, and mark anything unknown as `TODO(doctor)`. The build sprint story must be honest: weeks 1–3 went into narrowing scope (13-agent design → spine-first single workflow); the build happened at the end.

---

## 10. Existing assets (outside the repo)

- Evidex Round 1 deck (8 slides) and "Evidex: Full Pitch" deck (8 slides) as claude.ai artifacts.
- "Evidex Agent Map" page (13 steps: workspace, context, prompt, processing per agent).
- "Evidex Diabetes Spine" page (spine workflow, data model, Section 2 graph, lifecycle).
- Round 1 text: problem statement, solution description, technology answer, existing-work answer (building on NEXUS).

---

## 11. Suggested first task for you (Claude Code)

1. Read this file and the `evidex` repo; propose a plan with checkpoints for: spine schema → entry template/import → encyclopedia view → planner+composer on top → verifier number check → scope guard → export → hosting → eval script.
2. Wait for my OK, then build checkpoint 1 (spine schema + import + validation) and show me how to enter Section 2 Block A.
