# Architecture

The core design idea: everything is represented as structured, traceable **claims**, not free prose. Documents are composed only from claims the doctor has approved. One verified set of claims can be reused for many documents.

Agents run along a fixed LangGraph route and pass structured JSON between steps. Steps that can be checked with code are written as code, not as LLM calls.

## Offline: knowledge library

Curated sources → section-level chunks with page anchors → metadata (edition, study type, India / international) → retraction and superseded checks → hybrid index (BM25 + pgvector).

There are two libraries: an **evidence library** (guidelines and literature) and an **institution library** (templates and protocols).

## Online pipeline

| # | Step | Kind | What it does |
|---|------|------|--------------|
| 1 | Intake & Scope Guard | Agent | Declines out-of-scope requests and offers general evidence instead; strips identifiers |
| 2 | Context Assembler | Agent | Turns doctor input into patient_fact claims, each tied to an exact text span |
| 3 | Task Planner | Agent | Maps template sections to context, evidence queries (Indian and international), or "doctor must provide" |
| 4 | Evidence Retriever | Code | Hybrid search plus rerank |
| 5 | Sufficiency Judge | Agent | Decides whether the evidence answers the need; if not, returns "Not found in sources" |
| 6 | Claim Synthesizer | Agent | Writes evidence claims with verbatim quotes |
| 7 | Deviation Analyst | Agent | Writes comparison claims where Indian and international guidance differ |
| 8 | Verifier | Code + NLI | Checks the reference exists, the quote matches, the claim is entailed, the numbers match, and the source is not retracted or superseded. One retry, then the claim is flagged. |
| 9 | **Doctor review 1** | Human | Approve, edit or reject each claim; the approved claims become a reusable verified brief |
| 10 | Artifact Composers | Agent | Compose the document from approved claim IDs only |
| 11 | Fidelity & Completeness Checker | Code + NLI | Every sentence maps to an approved claim; no new numbers; required sections are present or marked "Not provided" |
| 12 | **Doctor review 2** | Human | Final review with click-to-source on every sentence |
| 13 | Export + Audit Log | Code | Signed-off DOCX, PDF or PPTX; every change logged |

## Hackathon MVP

Steps 1, 4, 5, 6, 8, 10 and 11, with a slide outline and a referral composer, and a single review screen.
