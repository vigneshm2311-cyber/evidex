# The spine: how to read and edit it

Two CSV files in `data/spine/` (private, not committed; empty templates in `data/spine_templates/`). Open them in Excel, Numbers or Google Sheets, edit, save as CSV, then run `scripts/spine.py check` and `import`.

## concepts.csv
`id` (prefix:name, prefix must match type: cond Condition, test Test, pop Population, rf RiskFactor, int Intervention, drug DrugClass, outcome Outcome, rule Rule, thr Threshold, comp Complication), `type`, `name`, `synonyms` (separate with `;`), `notes`.

## assertions.csv: one row per guideline statement
| column | meaning |
|---|---|
| id | e.g. `ada26-s2-a-dx-a1c` |
| subject_id, predicate, object_id | the statement, e.g. `cond:diabetes diagnosed_by test:a1c` |
| operator, value, value_high, unit | `>= 6.5 %`, or `range 5.7 6.4 %` |
| population_id | a `pop:` concept |
| condition_text | qualifiers, in your words (not pasted ADA text) |
| grade | evidence grade from the document. **Fill in.** |
| status | draft / proposed / accepted / rejected / superseded |
| section | ADA section number (2 or 3) |
| locator | Rec number or table, e.g. `Rec 2.1` or `Table 2.2`. **Fill in.** |
| page | page in Diabetes Care 49(Suppl 1). **Fill in.** |
| added_by, reviewed_by | names |
| doctor_checked | set `true` once you've checked the row against your 2026 copy |
| notes | `LOWER CONFIDENCE` marks rows to check first |

Allowed predicates: classified_as, subtype_of, diagnosed_by, has_threshold, requires_confirmation, unreliable_when, use_instead, screen_with, screening_starts_at, repeat_interval, risk_factor_for, defined_by, prevented_or_delayed_by, has_target, requires_monitoring_of.

Every change to an assertion is written to `change_log` with who made it (`--actor`).
