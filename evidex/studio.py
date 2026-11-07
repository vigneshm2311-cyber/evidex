"""The three-phase flow: Request → Building → Draft.

Phase 1  understand(): PII check, Scope Guard, read the files, say back what will be made, and
         stop on a topic the spine does not recognise. Nothing is built until the user confirms.
Phase 2  build(): spine rows for the topic + PubMed abstracts + passages from the uploads →
         plan → write per section → verify every statement (one rewrite) → save the draft.
Phase 3  is the review in the app: edit, remove, approve, download (drafts.py, export.py).

The original spine-only route (pipeline.run) is unchanged and still used by the evaluation.
"""
from __future__ import annotations

import time
import uuid
from typing import Iterator

from . import drafts, pipeline, sources
from .agents import steps, studio, verifier
from .llm import LLM, LLMError
from .spine import api

PHASE_STEPS = {  # plain-language names shown to the user
    "files": "Reading your files",
    "spine_lookup": "Checking the guideline",
    "research": "Finding recent research",
    "planner": "Planning the outline",
    "writing": "Writing",
    "done": "Done",
}


def _event(run_id, step, status, data, t0):
    return {"run_id": run_id, "step": step, "status": status, "data": data,
            "ms": int((time.monotonic() - t0) * 1000)}


def read_files(files: list[tuple[str, bytes]]) -> tuple[list[dict], list[str]]:
    """[(name, bytes)] -> ([{"name", "text"}], problems). Code only: no model sees a file
    before it has passed the PII check."""
    if len(files) > sources.MAX_FILES:
        return [], [f"Attach at most {sources.MAX_FILES} files."]
    out, problems = [], []
    for name, data in files:
        try:
            text = sources.read_upload(name, data)
        except sources.SourceError as e:
            problems.append(str(e))
            continue
        pii = sources.pii_problems(text)
        if pii:
            problems.append(f"{name} looks like it contains patient details ({', '.join(pii)}). "
                            "Evidex works with papers and teaching material only; remove the file.")
            continue
        out.append({"name": name, "text": text})
    return out, problems


def understand(conn, llm: LLM, request: str, persona: str, artifact: str, files: list[dict],
               *, log: bool = True) -> dict:
    """Phase 1. Returns {"status": ok | declined | not_found | blocked | error, ...}."""
    run_id = "u-" + uuid.uuid4().hex[:10]
    log_conn = conn if log else None
    request = (request or "").strip()
    if not request and not files:
        return {"status": "blocked", "message": "Type what you need or attach a paper or deck."}
    pii = sources.pii_problems(request)
    if pii:
        return {"status": "blocked", "message": "Your request looks like it contains patient details "
                f"({', '.join(pii)}). Evidex makes general teaching material only."}
    try:
        if request:
            note = (f"\n(Attached: {', '.join(f['name'] for f in files)})" if files else "")
            guard = steps.scope_guard(llm, request + note, run_id=run_id, log_conn=log_conn,
                                      system=steps.STUDIO_SCOPE_SYSTEM)
            if guard["label"] == "out_of_scope":
                return {"status": "declined", "reason": guard["reason"],
                        "general_query": guard.get("general_query", ""), "run_id": run_id}
        u = studio.understand(llm, request, persona, artifact, files, run_id=run_id, log_conn=log_conn)
    except LLMError as e:
        return {"status": "error", "message": f"Evidex could not read the request ({e}). Please try again."}
    u.update(persona=persona, artifact=artifact, request=request, run_id=run_id,
             files=[f["name"] for f in files])
    gap = topic_gap(conn, u["topic"])
    if gap:
        return {"status": "not_found", "understanding": u, "reason": gap, "run_id": run_id,
                "suggestions": suggestions(" ".join([request, u["topic"], u["title"]]))}
    return {"status": "ok", "understanding": u, "run_id": run_id}


# In-scope topics offered when a request is not covered. Each is a request Evidex can build;
# a test checks they all pass the topic rules. Keywords pick the closest ones to what was asked.
SUGGESTIONS = [
    ("Preventing type 2 diabetes at home: lifestyle steps for people with prediabetes",
     "home homecare care lifestyle prevent prevention diet food exercise weight living daily family self"),
    ("Healthy habits to lower diabetes risk: activity and weight goals",
     "home care lifestyle exercise activity weight diet food healthy habits obesity walking"),
    ("Understanding prediabetes: what the test results mean",
     "prediabetes results sugar levels glucose hba1c a1c understanding meaning patient education"),
    ("Who should be tested for diabetes, and how often",
     "screening testing test check risk who when often adults family"),
    ("Diagnosing diabetes: the tests and their cut-offs",
     "diagnosis diagnose criteria tests hba1c a1c glucose fasting ogtt sugar cut-offs thresholds"),
    ("When HbA1c is unreliable for diagnosing diabetes",
     "hba1c a1c unreliable anaemia anemia pregnancy kidney dialysis sickle hiv limitations"),
    ("After gestational diabetes: follow-up testing for mothers",
     "pregnancy pregnant gestational mother mothers postpartum women ogtt"),
    ("Type 1 diabetes: stages and autoantibodies",
     "type 1 children insulin autoimmune autoantibodies stages child kids"),
    ("Screening for type 2 diabetes in children and adolescents",
     "children child kids adolescents teenagers youth school paediatric pediatric"),
    ("Metformin for preventing type 2 diabetes in prediabetes",
     "medicine medication drug drugs metformin tablets pills treatment therapy"),
    ("Classification of diabetes: type 1, type 2, gestational and other types",
     "types classification kinds forms difference mody lada"),
]


def suggestions(text: str, n: int = 3) -> list[str]:
    """The n in-scope topics closest to what was asked (by shared words), for a 'Not found' screen."""
    words = {w.strip(".,?!:;()") for w in (text or "").lower().split()}
    scored = sorted(enumerate(SUGGESTIONS), key=lambda x: (-len(words & set(x[1][1].split())), x[0]))
    return [t for _, (t, _) in scored[:n]]


def topic_gap(conn, topic: str) -> str:
    """Why the spine (the grounding layer) cannot take this topic, or ''."""
    missing = pipeline.out_of_spine(topic)
    if missing:
        return f"Evidex does not cover {', '.join(missing)} yet."
    unknown = pipeline.unknown_topic(conn, topic)
    if unknown:
        return f"'{' '.join(unknown)}' is not part of what Evidex covers yet."
    return ""


def build(conn, llm: LLM, understanding: dict, files: list[dict], *, log: bool = True,
          save: bool = True, use_pubmed: bool = True) -> Iterator[dict]:
    """Phase 2, after the user confirmed `understanding`. Yields one event per step."""
    run_id = uuid.uuid4().hex[:12]
    log_conn = conn if log else None
    u = understanding
    persona, artifact = u["persona"], u["artifact"]
    query = " ".join([u["topic"], *u["key_points"]])

    t = time.monotonic()
    gap = topic_gap(conn, u["topic"])
    if gap:  # the user may have edited the topic after phase 1
        yield _event(run_id, "spine_lookup", "not_found", {"message": pipeline.NOT_FOUND, "reason": gap,
                                                           "suggestions": suggestions(u["topic"])}, t)
        return
    uploads = [sources.save_upload(conn, f["name"], f["text"]) for f in files]
    if uploads:
        yield _event(run_id, "files", "ok", {"files": [d["title"] for d in uploads]}, t)

    t = time.monotonic()
    cand = api.candidates_for(conn, u["topic"] + ". " + " ".join(u["key_points"]))
    if not cand["assertions"]:
        cand = api.candidates_for(conn, u["topic"])
    if not cand["assertions"]:
        yield _event(run_id, "spine_lookup", "not_found",
                     {"message": pipeline.NOT_FOUND, "reason": "The guideline layer has nothing on this topic."}, t)
        return
    yield _event(run_id, "spine_lookup", "ok", {"rows": len(cand["assertions"])}, t)

    t = time.monotonic()
    found, research_note = [], ""
    if use_pubmed:
        try:
            found = sources.pubmed_search(conn, u.get("search_terms") or u["topic"])
        except sources.SourceError as e:
            research_note = f"Recent research could not be reached ({e}); built from the guideline and your files."
    yield _event(run_id, "research", "ok" if not research_note else "unavailable",
                 {"papers": [{"id": d["id"], "title": d["title"], "year": d.get("year")} for d in found],
                  "note": research_note}, t)

    lit = [sources.as_row(d, query) for d in uploads + found]
    rows = cand["assertions"] + lit
    by_id = {a["id"]: a for a in rows}

    t = time.monotonic()
    try:
        outline = studio.plan(llm, u, artifact, persona, rows, run_id=run_id, log_conn=log_conn)
    except LLMError as e:
        yield _event(run_id, "planner", "error", {"error": str(e)}, t)
        return
    if not outline["sections"]:
        yield _event(run_id, "planner", "not_found",
                     {"message": pipeline.NOT_FOUND, "reason": "No source answers the key points."}, t)
        return
    yield _event(run_id, "planner", "ok", outline, t)

    sections = []
    for s in outline["sections"]:
        t = time.monotonic()
        sec_rows = [by_id[i] for i in s["assertion_ids"]]

        def rewrite(rws, feedback, heading=s["heading"]):
            return studio.write(llm, heading, persona, artifact, rws, run_id=run_id,
                                log_conn=log_conn, feedback=feedback)
        try:
            claims = studio.write(llm, s["heading"], persona, artifact, sec_rows, run_id=run_id, log_conn=log_conn)
            if artifact == "flowchart":
                claims = claims[:3]
            checked = verifier.verify_section(conn, llm, s["heading"], persona, claims, by_id,
                                              run_id=run_id, log_conn=log_conn, rewrite=rewrite)
            sec = {"heading": s["heading"], "claims": checked, "error": None}
            status = "ok" if all(c["verdict"]["ok"] for c in checked) else "flagged"
        except LLMError as e:
            sec, status = {"heading": s["heading"], "claims": [], "error": str(e)}, "error"
        sections.append(sec)
        pipeline._dedupe(sections)
        yield _event(run_id, f"writing:{s['heading']}", status, sec, t)

    data = {"request": u.get("request") or u["topic"], "artifact": artifact, "audience": persona,
            "title": outline["title"], "sections": [s for s in sections if s["claims"] or s["error"]],
            "model_not_covered": outline["not_covered"], "not_covered": [],
            "understanding": {**u, "research_note": research_note,
                              "papers": [d["id"] for d in found], "uploads": [d["id"] for d in uploads]}}
    cited = {i for s in sections for c in s["claims"] for i in c["assertion_ids"]}
    data["unused_rows"] = {s["heading"]: [i for i in s["assertion_ids"] if i not in cited]
                           for s in outline["sections"] if any(i not in cited for i in s["assertion_ids"])}
    if save and any(s["claims"] for s in data["sections"]):
        data["draft_id"] = drafts.save(conn, run_id, data)
    yield {"run_id": run_id, "step": "done", "status": "ok", "ms": 0, "data": data}
