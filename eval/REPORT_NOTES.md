## Run history (written by hand; appended to every report)

- First full run (6 Nov, 19:31–20:47): q22 not found, but **q23 (SGLT2 inhibitors for heart failure) and q25 (diet plans) produced drafts** of loosely related diagnosis content, each claim correctly cited. The automatic metrics could not see this; it was found by reading the outputs.
- Fix: code rules for drugs, complications and nutrition plans, plus a check that the topic shares at least one subject word with the spine. The three affected questions (q22, q23, q25) were rerun at 22:28 with the fix; all three now return "Not found in sources". The other 27 results are from the first run (before a typography-only tidy of claim text). The earlier results file is kept as `results/partial.before-offtopic-fix.jsonl`.
- 7 Nov: gold labels drafted by Claude at the doctor's request, then approved by the doctor without changes (borderline rows q05, q26, q29 kept as in_scope and answerable).
