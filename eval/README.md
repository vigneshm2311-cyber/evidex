# Evaluation

**Questions:** `eval/questions.csv` (open in Excel or Numbers). The `request` column was drafted by Claude; `drafted_as` is only Claude's intent and is never used as gold. **The doctor fills the gold columns:**

| column | values |
|---|---|
| `gold_scope` | `in_scope` (general guideline question) or `out_of_scope` (about a specific patient) |
| `gold_answerable` | `yes` if the spine (ADA 2026 Sections 2-3) should be able to answer it, `no` if Evidex should say "Not found in sources". Leave empty for out-of-scope rows. |
| `gold_must_cite` | optional: spine IDs that a good answer must cite, separated by `;` |
| `labelled_by` | your name |

Edit, add or delete questions freely. `scripts/eval.py` converts the sheet to `eval/questions.jsonl`.

**Run:** `.venv/bin/python scripts/eval.py` (all), `--part auto` (automatic metrics and the number-error injection test only), `--only q01,q03`, `--limit 5`. Results go to `eval/results/<time>.json` and `eval/REPORT.md`. Metrics that need gold labels are reported as "not computed" for unlabelled questions; nothing is estimated.
