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

## Status

Scaffold only. The build specification is in [docs/BUILD_PROMPT.md](docs/BUILD_PROMPT.md).

## Planned stack

Python · FastAPI · LangGraph · PostgreSQL + pgvector · Anthropic API · PubMed / Europe PMC / Crossref APIs · Next.js

## Repository layout

```
backend/   API, agents, verification, ingestion, export
frontend/  Artifact picker and review screens
data/      Open-access sources, synthetic cases, templates
eval/      Evaluation harness and test sets
docs/      Architecture, scope rules, build prompt
```
