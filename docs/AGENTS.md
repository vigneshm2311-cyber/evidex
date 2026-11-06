# Agents: what each one sees, can do, and is told

All agents run on a local model (Ollama, `qwen3:8b`, temperature 0, thinking off) on the doctor's Mac. Code (`evidex/pipeline.py`) runs a fixed route; no agent chooses the next step.

**What every agent has:** one system prompt and one block of text in; JSON matching a fixed schema out.
**What no agent has:** tools, internet, files, or database access. Code reads the spine and pastes only the rows an agent needs. No agent can write to the spine.
**Audit:** every call's exact input, output, model, attempts and time is stored in `agent_call` and shown in the app ("What each agent saw and returned").
**Failures:** malformed or invalid replies are retried once with the problems listed; then the step reports an error. Nothing is guessed.

| # | Step | Kind | Sees | Never sees | Returns | Code checks after |
|---|---|---|---|---|---|---|
| 1 | Scope Guard | LLM | the request text | spine, sources | `in_scope_topic` / `out_of_scope`, reason, a general version of the question | Red-flag rules (my patient, age, "should I start", dose for a person, his/her results) can only turn allow into decline. The suggested general question is rejected if it still carries patient details. |
| 2 | Out-of-spine rule | code | the request | — | stop with "Not found in sources" for doses/regimens, treatment/management, devices | The spine covers only diagnosis, classification, screening and prevention (ADA 2026 Sections 2–3). |
| 3 | Spine lookup | code | the request | — | accepted assertions that may be relevant (`candidates_for`) | Nothing matched → "Not found in sources"; the Planner never runs. Draft/proposed/rejected rows are never used. |
| 4 | Planner | LLM | topic, artifact, audience, candidate assertions (ID, plain sentence, qualifier) | grades, notes, history, patient facts | title, sections with assertion IDs, `not_covered` | IDs must come from the list (retry, then error). Empty outline → "Not found". A thin outline whose main subject the model itself lists as not covered → "Not found" (no filler). |
| 5 | Synthesizer (per section) | LLM | that section's assertions and the audience | other sections, patient facts, grades | claims: text, assertion IDs, numbers with units | IDs must belong to the section. Every number in a claim's text must exist in the cited rows; declared numbers must equal the spine value and unit; patient-directed wording is flagged; grades and anchors are copied from the spine by code. Near-duplicate claims are dropped. |

| 6 | Verifier (per section) | code + LLM support judge | one claim and **only** the spine sentences it cites; never the Synthesizer's prompt or reasoning | `supported` / `partly` / `not_supported` + reason | Code: cited rows still exist and are `accepted` (read from the database at check time); every number in the text matches a cited row's value and unit. A claim fails if any code check fails **or** the judge does not say `supported`. A failing claim is rewritten once by the Synthesizer with the problems listed; if it still fails it reaches the doctor flagged. |
| 7 | Doctor review | human | everything | — | approve / edit / reject each claim; sign off | An edit re-runs the Verifier. Flagged claims cannot be approved; claims with numbers need "numbers checked". A disputed number can become a `proposed` spine row (the spine is unchanged until accepted). Sign-off needs every claim decided, records name and time, and locks the draft. All decisions go to `change_log`. |

| 8 | Export | code only | the signed-off draft | — | PPTX / DOCX | Fidelity: every exported sentence equals an approved claim, no rejected/pending claim, no number outside approved claims, every approved claim present in the rendered file. Any failure blocks the download. Export logged with actor and SHA-256. |

The prompts are constants at the top of `evidex/agents/steps.py` (`SCOPE_SYSTEM`, `PLANNER_SYSTEM`, `SYNTH_SYSTEM`) and `evidex/agents/verifier.py` (`SUPPORT_SYSTEM`).

## Known limits
- The evidence for a claim is a spine assertion (structured value with its ADA anchor), not a verbatim quote; ADA text is not stored.
- The 8B model sometimes copies the spine's wording or makes small typos; the doctor review screen is where text is approved or edited.
- The LLM support judge can miss a wrong number (live test: it called "A1C 6.0–6.4 %" supported against a 5.7–6.4 % source). Numbers are therefore decided by code, which caught it.
- A run takes about 1.5–2.5 minutes on the Mac.
