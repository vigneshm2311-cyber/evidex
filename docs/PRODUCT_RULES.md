# Evidex product rules

The rules every screen, step and output follows. A rule marked **(code)** is enforced in code and
covered by a test; the others are design rules checked in review. `docs/SCOPE.md` still overrides
everything here.

## Design philosophy

1. **The doctor's language, not the app's.** Screens say *request, draft, sources, approve*. They
   never say *agent, spine row, planner, verifier, JSON, run id*.
2. **Time to value.** One screen per phase, one main button per screen. Sensible defaults so a
   request can be sent with a single sentence.
3. **Outcome first, detail on request.** The draft is shown as the finished thing (slides, handout,
   flowchart, one-pager). How it was checked lives under *Detailed review*.
4. **Never a dead end.** Every refusal says why in one sentence and offers what Evidex can do instead.
5. **Nothing leaves without approval.** Downloads appear only after the user approves the draft. **(code)**
6. **Quiet trust.** References are numbered links. Problems are shown only where the user must act.

## Phase 1 · Your request

- Inputs: a typed request, uploaded PDF papers, uploaded PPTX decks, or any mix. At least one is
  required. **(code)**
- Uploads: PDF or PPTX only, at most 3 files, 15 MB each; text is extracted by code, never by the
  model. **(code)**
- A PII check runs on the typed text and on every upload; anything that looks like a patient record
  is blocked before any model sees it. **(code)**
- A request about a specific patient is declined (Scope Guard plus code red flags), with a general
  question offered instead. **(code)**
- Evidex shows what it understood: title, topic, 3-6 key points, persona, format and files. The
  user edits or confirms it. **Nothing is built before this confirmation.** **(code)**
- A topic the spine does not recognise ends here with "Not found in sources" and a list of what
  Evidex covers. **(code)**

## Phase 2 · Building

- Progress is shown in plain steps: *Reading your files → Checking the guideline → Finding recent
  research → Writing → Checking every number*.
- The spine grounds the content: definitions and thresholds come from spine rows. **(code: spine
  numbers must equal the spine)**
- Research adds context: up to 5 recent PubMed abstracts (last 10 years, with abstracts) chosen by
  relevance, plus passages from the uploaded files.
- Every PMID, title, journal and link comes from the PubMed API response, never from the model.
  **(code)**
- Every claim cites at least one source; every number in a claim appears in a cited spine row or
  in the cited source's text. **(code)**
- A claim that fails a check is rewritten once, then shown to the user as *needs a fix*.
- If PubMed cannot be reached, the draft is built from the spine and the uploads, and the draft
  says research was unavailable.

## Phase 3 · Your draft

- The preview looks like the chosen format.
- Each statement can be edited (re-checked on save) or removed.
- *Approve and download* needs the user's name and a tick that they read the draft and checked its
  numbers. Statements that need a fix must be fixed or removed first. **(code)**
- Approval locks the draft; the file contains exactly the approved text. **(code: fidelity check)**
- References: numbered, with a link (PubMed or the guideline anchor). Every export ends with a
  short note on how it was made.

## Personas

| Persona | Voice | Depth | Rules |
|---|---|---|---|
| Medical students | Clear, exam-oriented | Criteria, thresholds, classic distinctions | Numbers with units every time |
| Residents and doctors (CME) | Concise, clinical | Newest evidence first, study design and size when given | Literature claims say what kind of study it was |
| Nurses and diabetes educators | Practical | What to check, when, and what the result means in general | Short sentences, action words |
| Patients and public | Plain language (about grade 8), warm | General education only | No individual advice, no drug doses; explain every number; ends with "General information, not advice for any individual. Talk to your doctor." **(code)** |

## Formats

| Format | Shape | Rules |
|---|---|---|
| Slides (PPTX) | Title slide, 4-6 content slides, references, how it was made | At most 6 bullets per slide; long sections continue on a new slide **(code)** |
| Handout (DOCX) | Title, 3-5 short sections, references | Bullets, not paragraphs |
| Flowchart (PPTX) | 3-6 ordered steps drawn as boxes with arrows | Steps and text come only from approved statements **(code)**; at most 3 short statements per step |
| One-page summary (PDF) | Title, 3-4 sections, references | Must fit one A4 page; the font shrinks to 7.5 pt at the smallest, otherwise export is blocked with a message to remove statements **(code)** |

## Never

- Never invent a reference, PMID, number or study result.
- Never advise on an individual patient.
- Never show internal identifiers, prompts or model names on the main screens (the audit log keeps them).
