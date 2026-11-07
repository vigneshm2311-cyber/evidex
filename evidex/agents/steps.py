"""The three LLM agents (Scope Guard, Planner, Synthesizer) and the code checks around them.

Each agent gets one prompt and returns JSON matching a schema. Agents have no tools and
no database access; code decides what text they see and checks everything they return.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..llm import LLM, LLMError, call_logged
from ..spine import api

ARTIFACTS = {
    "slides": "teaching slide deck (4-6 content slides)",
    "handout": "teaching handout (3-5 short sections)",
}
AUDIENCES = [
    "Medical students",
    "Junior doctors and residents",
    "General practitioners",
    "Nurses and diabetes educators",
]

# ---------------- 1. Scope Guard ----------------

SCOPE_SYSTEM = """You screen requests sent to a diabetes guideline encyclopedia used by doctors.
The encyclopedia only reports what guidelines say in general. It must not be used for
individual patient care.

Label the request:
- "out_of_scope": anything about a specific or described patient: diagnosing them, what they
  should take or receive, a dose for them, their risk, or interpreting their results.
  Mentioning a patient's age, sex, numbers or history makes it patient-specific.
- "in_scope_topic": a general question or teaching topic about what guidelines say. Topics
  about a GROUP of people are general and in scope (e.g. adults, Indian or Asian adults,
  pregnant women, children, people with prediabetes).

Examples:
- "Diagnosing diabetes in Indian adults" -> in_scope_topic (a population, not a patient)
- "Teaching slides on screening in pregnancy for nurses" -> in_scope_topic
- "When is HbA1c unreliable?" -> in_scope_topic
- "My patient's HbA1c is 6.8, does she have diabetes?" -> out_of_scope (interprets a patient's result)
- "What dose of metformin should I give a 60-year-old man?" -> out_of_scope (dose for a person)

Always also write "general_query": the same subject as the request, rewritten as a general,
guideline-level question with every patient detail removed. Keep the request's own drug, test
or condition; do not change the subject.
Reply in JSON only."""

SCOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["in_scope_topic", "out_of_scope"]},
        "reason": {"type": "string"},
        "general_query": {"type": "string"},
    },
    "required": ["label", "reason", "general_query"],
}

# Code can only make the guard stricter: any of these turns an allow into a decline.
RED_FLAGS = [
    (r"\b(my|this|our|the) (patient|pt|client)\b", "refers to a specific patient"),
    (r"\bpatient (who|with|aged|age|has|is)\b", "describes a specific patient"),
    (r"\b\d{1,3}[- ]?(year|yr|y)s?[- ]?old\b|\b\d{1,3}\s?(yo|y/o)\b", "gives a patient's age"),
    (r"\bshould (i|we) (start|give|prescribe|use|increase|decrease|stop|switch|add)\b",
     "asks what to do for a patient"),
    (r"\b(what|which) (dose|dosage)\b|\bhow much .* (give|start|prescribe)\b", "asks for a dose"),
    (r"\b(his|her|their) (a1c|hba1c|glucose|sugar|fpg|bmi|results?)\b", "interprets a patient's results"),
    (r"\bdoes (he|she|my patient) have\b", "asks for a diagnosis of a patient"),
]


def red_flags(text: str) -> list[str]:
    t = text.lower()
    return [why for pat, why in RED_FLAGS if re.search(pat, t)]


def _general_query_problems(general: str, request: str) -> list[str]:
    problems = [f"general_query still {why}" for why in red_flags(general)]
    leaked = numbers_in(general) & numbers_in(request)
    if leaked:
        problems.append("general_query repeats the patient's numbers: "
                        + ", ".join(format(n, "f") for n in sorted(leaked)))
    if re.search(r"\bpatient\b", general.lower()):
        problems.append("general_query mentions a patient")
    return problems


def scope_guard(llm: LLM, request: str, *, run_id: str, log_conn=None) -> dict:
    def check(out):
        # Only the suggested general question is checked here; the label is checked below.
        return _general_query_problems(out["general_query"], request)

    try:
        out = call_logged(llm, SCOPE_SYSTEM, f"Request:\n{request}", SCOPE_SCHEMA,
                          step="scope_guard", run_id=run_id, validate=check, log_conn=log_conn)
        general = out["general_query"].strip()
    except LLMError as e:
        if "general_query" not in str(e):
            raise
        # The label is still usable; only the suggestion failed twice. Drop it rather than
        # show a "general" question that carries patient details.
        out = llm_last_reply(llm) or {"label": "out_of_scope", "reason": str(e)}
        general = ""
    flags = red_flags(request)
    result = {"label": out["label"], "reason": out["reason"], "general_query": general,
              "code_flags": flags, "decided_by": "model"}
    if flags and out["label"] != "out_of_scope":
        result.update(label="out_of_scope", decided_by="code rule",
                      reason="Declined by rule: request " + "; ".join(flags) + ".")
    return result


def llm_last_reply(llm) -> dict | None:
    return getattr(llm, "last_reply", None)


# ---------------- 3. Planner ----------------

PLANNER_SYSTEM = """You plan teaching material for doctors from a curated guideline knowledge base.
You are given numbered spine assertions (ID | statement | qualifier). They are the ONLY
material available.

Make an outline for the requested artifact and audience:
- Each section lists the assertion IDs it will use, copied exactly from the list.
- Use only assertions relevant to the topic; it is fine to leave many unused.
- Put each assertion in the section that matches it (e.g. pregnancy thresholds in a pregnancy
  section, not under limitations of a test).
- If part of the topic is not covered by any assertion, do not invent a section for it:
  list it in "not_covered" instead.
- If the main subject of the topic is not covered, return an empty "sections" list. Do not
  fill the outline with loosely related background just to have something.
- Do not answer anything, add facts, or write content. Headings only.
Reply in JSON only."""

PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {"heading": {"type": "string"},
                           "assertion_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["heading", "assertion_ids"]}},
        "not_covered": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "sections", "not_covered"],
}


def assertion_line(a: dict) -> str:
    line = f"{a['id']} | {api.statement(a)}"
    if a.get("condition_text"):
        line += f" | {a['condition_text']}"
    return line


def planner(llm: LLM, topic: str, artifact: str, audience: str, candidates: list[dict],
            *, run_id: str, log_conn=None) -> dict:
    valid = {a["id"] for a in candidates}

    def check(out):
        bad = sorted({i for s in out["sections"] for i in s["assertion_ids"]} - valid)
        return [f"these IDs are not in the list: {', '.join(bad)}"] if bad else []

    user = (f"Artifact: {ARTIFACTS[artifact]}\nAudience: {audience}\nTopic: {topic}\n\n"
            "Spine assertions:\n" + "\n".join(assertion_line(a) for a in candidates))
    out = call_logged(llm, PLANNER_SYSTEM, user, PLANNER_SCHEMA, step="planner",
                      run_id=run_id, validate=check, log_conn=log_conn)
    sections = []
    for s in out["sections"]:
        ids = [i for i in dict.fromkeys(s["assertion_ids"]) if i in valid]
        if ids:
            sections.append({"heading": s["heading"].strip(), "assertion_ids": ids})
    not_covered = [x for x in out["not_covered"] if x.strip()]
    main_missing = [x for x in not_covered if same_subject(x, topic)]
    used = sum(len(s["assertion_ids"]) for s in sections)
    if main_missing and used <= 3:
        # The model says the main subject is missing and the outline is thin filler:
        # report Not found rather than pad. A full outline with a noted gap is kept.
        sections = []
    return {"title": out["title"].strip(), "sections": sections, "not_covered": not_covered,
            "main_subject_missing": bool(main_missing) and not sections}


STOP = {"the", "a", "an", "of", "for", "in", "on", "and", "or", "to", "with", "what", "when",
        "how", "is", "are", "does", "do", "about", "slides", "slide", "teaching", "handout",
        "deck", "guideline", "guidelines", "ada", "says", "say", "recommend", "recommendations"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP}


def same_subject(item: str, topic: str) -> bool:
    """True if a 'not covered' item names most of the topic itself."""
    t, i = _words(topic), _words(item)
    return bool(t) and len(t & i) / len(t) >= 0.7


# ---------------- 4. Synthesizer ----------------

SYNTH_SYSTEM = """You turn guideline knowledge-base assertions into short, neutral claims for a
teaching document.

Rules:
- Use ONLY the assertions given. Never add outside knowledge, numbers, drugs or advice.
- Write natural, plain English for the audience; do not copy the "X is diagnosed by Y" or
  "unreliable in" wording of the assertions. Keep normal capitalisation (A1C, HIV, OGTT).
- One idea per claim. Assertions that share a pattern (e.g. several conditions in which the
  same test is unreliable) may be combined into ONE claim that lists them all and cites
  every ID it uses.
- Each claim lists the assertion IDs it rests on, copied exactly.
- List every number that appears in the claim text in "numbers", with its unit and the
  assertion ID it comes from. Copy numbers exactly; do not convert units.
- Report what the guideline says ("ADA states...", "...is diagnosed when..."). Never say what a
  particular patient should do or receive.
- If an assertion cannot be stated without adding facts, leave it out.
Reply in JSON only."""

SYNTH_SCHEMA = {
    "type": "object",
    "properties": {"claims": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "assertion_ids": {"type": "array", "items": {"type": "string"}},
            "numbers": {"type": "array", "items": {
                "type": "object",
                "properties": {"value": {"type": "number"}, "unit": {"type": "string"},
                               "assertion_id": {"type": "string"}},
                "required": ["value", "unit", "assertion_id"]}},
        },
        "required": ["text", "assertion_ids", "numbers"]}}},
    "required": ["claims"],
}


def synthesizer(llm: LLM, heading: str, audience: str, assertions: list[dict],
                *, run_id: str, log_conn=None, feedback: str | None = None) -> list[dict]:
    valid = {a["id"] for a in assertions}

    def check(out):
        problems = []
        for i, c in enumerate(out["claims"], 1):
            if not c["text"].strip():
                problems.append(f"claim {i} has no text")
            if not c["assertion_ids"]:
                problems.append(f"claim {i} lists no assertion IDs")
            bad = set(c["assertion_ids"]) - valid
            if bad:
                problems.append(f"claim {i} uses IDs not given: {', '.join(sorted(bad))}")
        return problems

    user = (f"Section: {heading}\nAudience: {audience}\n\nAssertions:\n"
            + "\n".join(assertion_line(a) for a in assertions))
    if feedback:
        user += ("\n\nA checker rejected an earlier claim from these assertions:\n" + feedback
                 + "\nWrite the claim again so that it states only what the assertions say.")
    out = call_logged(llm, SYNTH_SYSTEM, user, SYNTH_SCHEMA,
                      step=f"synthesizer:{heading}" + (":retry" if feedback else ""),
                      run_id=run_id, validate=check, log_conn=log_conn)
    names = {a.get(k) for a in assertions for k in ("subject_name", "object_name", "population_name")}
    for c in out["claims"]:
        c["text"] = tidy(c["text"], names - {None})
    return out["claims"]


# Concept names that start with a proper noun keep their capital letter.
PROPER_START = {"Asian", "American", "African", "Hispanic", "Latino", "Native", "Pacific", "Down", "Cushing",
                "Wolfram", "Turner", "Klinefelter", "Prader", "Indian", "Black"}


def tidy(text: str, names: set[str]) -> str:
    """Typography only: '6.4 %' -> '6.4%', and a spine concept name copied mid-sentence
    ('when Glycated haemoglobin') loses its capital. Words and numbers are otherwise unchanged."""
    text = re.sub(r"(\d) %", r"\1%", text)
    for n in sorted(names, key=len, reverse=True):
        first = n.split()[0]
        if len(first) > 1 and first[1:].islower() and first not in PROPER_START:
            text = re.sub(r"(?<=[a-z0-9,;:)] )" + re.escape(n), n[0].lower() + n[1:], text)
    return text


# ---------------- code checks on claims ----------------

NUM_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\d])")
# A number followed by a unit, e.g. "6.5%", "126 mg/dL", "35 years", "23 kg/m2"
NUM_UNIT_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(%|mg/dl|mmol/mol|mmol/l|mmhg|kg/m2|kg/m²|min(?:utes)?/week|"
    r"years?|yrs?|months?|weeks?|percentile|autoantibod(?:y|ies))", re.I)
PATIENT_WORDING = re.compile(
    r"\b(your|this|the|a) patient (should|must|needs)\b|\byou should (start|prescribe|give)\b"
    r"|\bwe recommend\b|\bprescribe\b", re.I)


def _d(x) -> Decimal | None:
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None


def numbers_in(text: str) -> set[Decimal]:
    return {Decimal(m) for m in NUM_RE.findall(text or "")}


def _unit(u: str | None) -> str:
    """Comparable unit key: lower case, no spaces, first word, no plural."""
    u = re.sub(r"\s+", " ", (u or "").lower()).strip().replace("²", "2")
    u = u.split(" ")[0] if u else ""
    u = {"yrs": "years", "yr": "years", "year": "years", "month": "months", "week": "weeks",
         "minutes/week": "min/week", "autoantibody": "autoantibodies"}.get(u, u)
    return u


def check_text(text: str, cited: list[dict]) -> dict:
    """Code checks on a claim's text against the spine rows it cites.

    Numbers are read from the text itself (not from what the model says it used), so the
    same check applies to model drafts and doctor edits.
    """
    issues = []
    allowed: set[Decimal] = set()
    values: dict[Decimal, list[dict]] = {}
    for a in cited:
        for v in (a["value"], a["value_high"]):
            if v is not None:
                values.setdefault(Decimal(v), []).append(a)
        allowed |= numbers_in(" ".join(filter(None, [api.statement(a), a.get("condition_text") or ""])))
    allowed |= set(values)

    units_after = {Decimal(n): u for n, u in NUM_UNIT_RE.findall(text or "")}
    number_checks = []
    for n in sorted(numbers_in(text)):
        unit = units_after.get(n)
        rows = values.get(n, [])
        if rows:
            same_unit = [a for a in rows if unit is None or _unit(unit) == _unit(a["unit"])]
            if same_unit:
                number_checks.append({"value": format(n, "f"), "unit": unit or "", "ok": True,
                                      "assertion_id": same_unit[0]["id"], "why": ""})
            else:
                a = rows[0]
                number_checks.append({"value": format(n, "f"), "unit": unit or "", "ok": False,
                                      "assertion_id": a["id"],
                                      "why": f"says {format(n, 'f')} {unit}; spine says "
                                             f"{api.format_value(a)} ({a['id']})"})
        elif n in allowed:
            continue  # part of a name or qualifier, e.g. "75-g OGTT", "at least 8 h"
        else:
            # Wrong value: point at the cited row with the same unit, if there is one
            near = [a for a in cited if a["value"] is not None
                    and (unit is None or _unit(unit) == _unit(a["unit"]))]
            a = near[0] if near else None
            why = (f"says {format(n, 'f')} {unit or ''}".rstrip() + f"; spine says {api.format_value(a)} ({a['id']})"
                   if a else f"{format(n, 'f')} is not in the cited spine rows")
            number_checks.append({"value": format(n, "f"), "unit": unit or "", "ok": False,
                                  "assertion_id": a["id"] if a else None, "why": why,
                                  "range": bool(a and a["operator"] == "range")})
    stray = [c["value"] for c in number_checks if not c["ok"] and c["value"] and _d(c["value"]) not in allowed]
    if stray:
        issues.append("number(s) not in the cited spine rows: " + ", ".join(stray))
    issues += [c["why"] for c in number_checks if not c["ok"]]
    if PATIENT_WORDING.search(text or ""):
        issues.append("patient-directed wording")
    return {"number_checks": number_checks, "issues": issues}


def check_claim(claim: dict, by_id: dict[str, dict]) -> dict:
    """Code verdict for one claim. Grade and anchors come from the spine, never the model."""
    cited = [by_id[i] for i in claim["assertion_ids"] if i in by_id]
    res = check_text(claim["text"], cited)
    issues = list(res["issues"])
    if not cited:
        issues.insert(0, "cites no known assertion")
    return {
        **claim,
        "number_checks": res["number_checks"],
        "issues": issues,
        "ok": not issues,
        "grades": [a["grade"] or "TODO(doctor)" for a in cited],
        "anchors": [{"assertion_id": a["id"], "anchor": api.anchor_text(a),
                     "doctor_checked": a["doctor_checked"]} for a in cited],
    }
