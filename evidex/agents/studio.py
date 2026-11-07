"""Agent steps for the three-phase flow: Understand (phase 1), Plan and Write (phase 2).

Same contract as steps.py: one prompt, JSON out, checked by code, no tools, no database access.
The material the agents see is a mix of spine rows (the grounding layer) and literature sources
(PubMed abstracts, passages from uploaded files); both are cited by ID.
"""
from __future__ import annotations

from ..llm import LLM, call_logged
from . import steps

# ---------------- Understand ----------------

UNDERSTAND_SYSTEM = """You help a doctor prepare teaching material on diabetes. Read what the doctor
asked and any files they attached, and say back what will be made.

Return:
- "title": a short title for the finished material.
- "topic": the general guideline-level subject in a few words (e.g. "diagnosing diabetes",
  "screening for type 2 diabetes in children", "HbA1c limitations"). No patient details.
- "key_points": 3-6 questions the material will answer, in the doctor's language.
- "search_terms": 2-4 short concepts for a PubMed search, each 1-3 words, most important first
  (e.g. ["HbA1c", "diagnosis", "diabetes", "India"]). No boolean operators.
- "from_files": one sentence on what the attached files are about, or "" if there are none.
Never answer the questions and never add facts. Reply in JSON only."""

UNDERSTAND_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "topic": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "search_terms": {"type": "array", "items": {"type": "string"}},
        "from_files": {"type": "string"},
    },
    "required": ["title", "topic", "key_points", "search_terms", "from_files"],
}


def understand(llm: LLM, request: str, persona: str, artifact: str, files: list[dict],
               *, run_id: str, log_conn=None) -> dict:
    """files: [{"name", "text"}]. Returns the understanding the user confirms in phase 1."""
    def check(out):
        problems = []
        if not out["topic"].strip():
            problems.append("topic is empty")
        if steps.red_flags(out["topic"]):
            problems.append("topic must be general, with no patient details")
        if not 2 <= len([k for k in out["key_points"] if k.strip()]) <= 8:
            problems.append("give 3-6 key points")
        if not [t for t in out["search_terms"] if t.strip()]:
            problems.append("give 2-4 search concepts")
        return problems

    parts = [f"Doctor's request: {request or '(none: use the attached files)'}",
             f"Audience: {persona}", f"Format: {steps.ARTIFACTS[artifact]}"]
    for f in files:
        parts.append(f"Attached file '{f['name']}' (start):\n{f['text'][:2500]}")
    out = call_logged(llm, UNDERSTAND_SYSTEM, "\n\n".join(parts), UNDERSTAND_SCHEMA, step="understand",
                      run_id=run_id, validate=check, log_conn=log_conn)
    return {"title": out["title"].strip(), "topic": out["topic"].strip(),
            "key_points": [k.strip() for k in out["key_points"] if k.strip()][:6],
            "search_terms": [t.strip() for t in out["search_terms"] if t.strip()][:4],
            "from_files": out["from_files"].strip() if files else ""}


# ---------------- Plan ----------------

PLAN_SYSTEM = """You plan teaching material on diabetes. You are given numbered sources:
- guideline entries (IDs like ada26-...): the curated ADA 2026 knowledge base. These are the
  ground truth for definitions, criteria and thresholds.
- research sources (IDs starting pmid: or up:): published abstracts or passages from files the
  doctor attached. They add context: findings, numbers from studies, practical points.

Make an outline for the requested format and audience that answers the key points:
- Each section lists the source IDs it will use, copied exactly from the list.
- Build the core from guideline entries; add research sources where they add real context.
- Use only sources relevant to the topic; leave the rest unused. Ignore research sources that are
  off-topic.
- Put each source where it belongs. If a key point is not covered by any source, list it in
  "not_covered"; do not invent a section for it.
- For a flowchart, sections are the ordered steps of the process (first step first).
- Headings only: no content. Reply in JSON only."""


def plan(llm: LLM, understanding: dict, artifact: str, persona: str, rows: list[dict],
         *, run_id: str, log_conn=None) -> dict:
    valid = {a["id"] for a in rows}

    def check(out):
        bad = sorted({i for s in out["sections"] for i in s["assertion_ids"]} - valid)
        problems = [f"these IDs are not in the list: {', '.join(bad)}"] if bad else []
        if out["sections"] and not any(not i.startswith(("pmid:", "up:"))
                                       for s in out["sections"] for i in s["assertion_ids"]):
            problems.append("use at least one guideline entry (ada26-...) as the core")
        return problems

    user = (f"Format: {steps.ARTIFACTS[artifact]}\nAudience: {persona}\nTopic: {understanding['topic']}\n"
            "Key points:\n" + "\n".join(f"- {k}" for k in understanding["key_points"])
            + "\n\nSources:\n" + "\n".join(_short_line(a) for a in rows))
    out = call_logged(llm, PLAN_SYSTEM, user, steps.PLANNER_SCHEMA, step="planner",
                      run_id=run_id, validate=check, log_conn=log_conn)
    sections = []
    for s in out["sections"]:
        ids = [i for i in dict.fromkeys(s["assertion_ids"]) if i in valid]
        if ids:
            sections.append({"heading": s["heading"].strip(), "assertion_ids": ids})
    limit = {"flowchart": 6, "onepager": 4}.get(artifact)
    if limit:
        sections = sections[:limit]
    return {"title": understanding.get("title") or out["title"].strip(), "sections": sections,
            "not_covered": [x for x in out["not_covered"] if x.strip()]}


def _short_line(a: dict) -> str:
    if a.get("kind") in ("pubmed", "upload"):
        return f"{a['id']} | {a['title']} | {(a.get('passage') or a['body'])[:260]}"
    return steps.assertion_line(a)


# ---------------- Write ----------------

WRITE_SYSTEM = """You write short, accurate statements for teaching material on diabetes.

Rules:
- Use ONLY the sources given. Never add outside knowledge, numbers, drugs, studies or advice.
- Guideline entries (ada26-...) are the ground truth for definitions, criteria and thresholds.
  Research sources (pmid:, up:) add context: say what the study or paper found ("A 2024 review
  reported...", "In a cohort of ... adults ..."). Never present a single study as a guideline rule,
  and never contradict a guideline entry.
- Write for the audience in the style given. One idea per statement. Keep normal capitalisation
  (A1C, HbA1c, OGTT).
- Each statement lists the source IDs it rests on, copied exactly.
- List every number in the statement in "numbers" with its unit and source ID. Copy numbers
  exactly as the source gives them; do not convert units or round.
- Report what sources say. Never say what a particular patient should do or receive.
- If a source cannot be stated without adding facts, leave it out.
Reply in JSON only."""

FORMAT_HINT = {
    "slides": "Slide bullets: at most about 25 words each.",
    "handout": "Handout bullets: one or two sentences each.",
    "flowchart": "Flowchart step: at most 3 statements, each under 18 words, phrased as a check or decision point.",
    "onepager": "Quick-reference sheet: at most 3 statements, each under 22 words.",
}


def write(llm: LLM, heading: str, persona: str, artifact: str, rows: list[dict],
          *, run_id: str, log_conn=None, feedback: str | None = None) -> list[dict]:
    valid = {a["id"] for a in rows}

    def check(out):
        problems = []
        for i, c in enumerate(out["claims"], 1):
            if not c["text"].strip():
                problems.append(f"statement {i} has no text")
            if not c["assertion_ids"]:
                problems.append(f"statement {i} lists no source IDs")
            bad = set(c["assertion_ids"]) - valid
            if bad:
                problems.append(f"statement {i} uses IDs not given: {', '.join(sorted(bad))}")
        return problems

    style = steps.PERSONA_STYLE.get(persona, "clear and accurate")
    user = (f"Section: {heading}\nAudience: {persona} ({style})\n{FORMAT_HINT.get(artifact, '')}\n\n"
            "Sources:\n" + "\n".join(steps.assertion_line(a) for a in rows))
    if feedback:
        user += ("\n\nA checker rejected an earlier statement from these sources:\n" + feedback
                 + "\nWrite it again so that it states only what the sources say.")
    out = call_logged(llm, WRITE_SYSTEM, user, steps.SYNTH_SCHEMA,
                      step=f"synthesizer:{heading}" + (":retry" if feedback else ""),
                      run_id=run_id, validate=check, log_conn=log_conn)
    names = {a.get(k) for a in rows for k in ("subject_name", "object_name", "population_name")}
    for c in out["claims"]:
        c["text"] = steps.tidy(c["text"], names - {None})
    return out["claims"]
