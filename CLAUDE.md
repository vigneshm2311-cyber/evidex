# Evidex: notes for Claude Code

- The project context is in `docs/CONTEXT.md` (the doctor's brief). Read it before planning any work. Spine details: `docs/SPINE.md`.
- `docs/SCOPE.md` is binding. Never build anything that breaks it. Flag any conflict to the user instead.
- `docs/ARCHITECTURE.md` describes the agent pipeline.
- Plan first and wait for approval before writing code.
- Never invent citations, PMIDs, DOIs, gold answers or evaluation results.
- Record architecture choices in `DECISIONS.md`.
- Secrets go in `.env` (see `.env.example`). Never commit secrets.
