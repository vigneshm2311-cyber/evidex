"""Fixed route for one request. No agent chooses the next step; this function does.

  1 Scope Guard (LLM)  ->  declined? stop
  2 Spine lookup (code) -> nothing? "Not found in sources", stop
  3 Planner (LLM)       -> no covered sections? "Not found in sources", stop
  4 Synthesizer (LLM) per section
  5 Verifier per section: code checks + support judge (LLM); a failing claim is rewritten
    once, then left flagged for the doctor
  6 Draft saved for doctor review (nothing is exported before sign-off)

run() yields one event per step so the UI can show progress.
"""
from __future__ import annotations

import time
import uuid
from typing import Iterator

from . import drafts
from .agents import steps, verifier
from .llm import LLM, LLMError
from .spine import api

NOT_FOUND = "Not found in sources"
SPINE_COVERS = ("diagnosis, classification, screening and prevention of diabetes "
                "(ADA Standards of Care 2026, Sections 2-3)")

# Kinds of question the spine cannot answer, whatever concepts they mention: it holds no
# dosing, treatment or device statements. Checked by code before any model sees the topic.
OUT_OF_SPINE = [
    (r"\bdos(e|es|age|ages|ing)\b|\btitrat|\bregimens?\b|\b\d+\s?mg\b|\bunits? per\b", "doses or regimens"),
    (r"\btreat(ment|ing|s)?\b|\bmanag(e|ement|ing)\b|\btherap(y|ies)\b(?! for prevention)|\bglycaemic targets?\b|\bglycemic targets?\b",
     "treatment or management"),
    (r"\bpumps?\b|\bcgm\b|continuous glucose monitor", "devices"),
]


def out_of_spine(text: str) -> list[str]:
    import re
    t = text.lower()
    return [why for pat, why in OUT_OF_SPINE if re.search(pat, t)]


def _dedupe(sections: list[dict]) -> None:
    """Drop claims that repeat an earlier claim (same spine rows, mostly the same words)."""
    seen: list[tuple[frozenset, set]] = []
    for s in sections:
        keep = []
        for c in s["claims"]:
            ids = frozenset(c["assertion_ids"])
            words = set(c["text"].lower().split())
            if any(ids == i and len(words & w) / max(len(words | w), 1) >= 0.5 for i, w in seen):
                continue
            seen.append((ids, words))
            keep.append(c)
        s["claims"] = keep


def run(conn, llm: LLM, request: str, artifact: str = "slides",
        audience: str = steps.AUDIENCES[0], log: bool = True, save: bool = True) -> Iterator[dict]:
    run_id = uuid.uuid4().hex[:12]
    log_conn = conn if log else None

    def event(step, status, data, t0):
        return {"run_id": run_id, "step": step, "status": status, "data": data,
                "ms": int((time.monotonic() - t0) * 1000)}

    t = time.monotonic()
    try:
        guard = steps.scope_guard(llm, request, run_id=run_id, log_conn=log_conn)
    except LLMError as e:
        yield event("scope_guard", "error", {"error": str(e)}, t)
        return
    if guard["label"] == "out_of_scope":
        yield event("scope_guard", "declined", guard, t)
        return
    yield event("scope_guard", "ok", guard, t)

    t = time.monotonic()
    missing = out_of_spine(request)
    if missing:
        yield event("spine_lookup", "not_found", {
            "message": NOT_FOUND, "concepts": [], "predicates": [], "assertions": [],
            "not_covered": [f"{m} (the spine covers only {SPINE_COVERS})" for m in missing]}, t)
        return
    cand = api.candidates_for(conn, request)
    if not cand["assertions"]:
        yield event("spine_lookup", "not_found", {**cand, "message": NOT_FOUND}, t)
        return
    yield event("spine_lookup", "ok", {**cand, "assertion_ids": [a["id"] for a in cand["assertions"]]}, t)
    by_id = {a["id"]: a for a in cand["assertions"]}

    t = time.monotonic()
    try:
        plan = steps.planner(llm, request, artifact, audience, cand["assertions"],
                             run_id=run_id, log_conn=log_conn)
    except LLMError as e:
        yield event("planner", "error", {"error": str(e)}, t)
        return
    if not plan["sections"]:
        yield event("planner", "not_found", {**plan, "message": NOT_FOUND}, t)
        return
    yield event("planner", "ok", plan, t)

    sections = []
    for s in plan["sections"]:
        t = time.monotonic()
        rows = [by_id[i] for i in s["assertion_ids"]]
        try:
            claims = steps.synthesizer(llm, s["heading"], audience, rows,
                                       run_id=run_id, log_conn=log_conn)
            checked = verifier.verify_section(conn, llm, s["heading"], audience, claims, by_id,
                                              run_id=run_id, log_conn=log_conn)
            sec = {"heading": s["heading"], "claims": checked, "error": None}
            status = "ok" if all(c["verdict"]["ok"] for c in checked) else "flagged"
        except LLMError as e:
            sec = {"heading": s["heading"], "claims": [], "error": str(e)}
            status = "error"
        sections.append(sec)
        _dedupe(sections)
        yield event(f"synthesizer:{s['heading']}", status, sec, t)

    data = {
        "request": request, "artifact": artifact, "audience": audience,
        "title": plan["title"], "sections": [s for s in sections if s["claims"] or s["error"]],
        # The model's own list of gaps is unreliable when it did produce an outline, so it is
        # kept for the audit log but not shown as fact.
        "model_not_covered": plan["not_covered"], "not_covered": []}
    if save and any(s["claims"] for s in data["sections"]):
        data["draft_id"] = drafts.save(conn, run_id, data)
    yield {"run_id": run_id, "step": "done", "status": "ok", "ms": 0, "data": data}
