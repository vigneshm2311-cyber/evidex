"""Verifier: code checks plus a support judge, run on every claim.

Code checks (deterministic): cited rows exist, are still accepted, numbers and units match
the spine, no patient-directed wording. The support judge (LLM) sees one claim and ONLY the
spine sentences it cites, never the Synthesizer's prompt or reasoning, and says whether the
sources support it.
"""
from __future__ import annotations

from .. import sources as srcs
from ..llm import LLM, LLMError, call_logged
from ..spine import api
from . import steps

SUPPORT_SYSTEM = """You check claims written for a teaching document against their sources.
The sources are entries from a guideline knowledge base and, for some claims, a published abstract
or a passage from an uploaded paper. Judge ONLY against the sources given with each claim, not your
own knowledge.

The sources are listed once; each claim names the IDs of its own sources. Judge each claim only
against ITS sources. For each claim answer:
- "supported": everything the claim says is stated in its sources (rewording is fine).
- "partly": part is supported, but the claim adds, broadens or changes something.
- "not_supported": the sources do not say this, or a number differs.
Give a short reason. Reply in JSON only."""

SUPPORT_SCHEMA = {
    "type": "object",
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object",
        "properties": {"claim": {"type": "integer"},
                       "verdict": {"type": "string", "enum": ["supported", "partly", "not_supported"]},
                       "reason": {"type": "string"}},
        "required": ["claim", "verdict", "reason"]}}},
    "required": ["verdicts"],
}


def current_rows(conn, ids: list[str]) -> dict[str, dict]:
    """The cited rows as they are in the database now (not as they were at drafting time)."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(api.ASSERTION_SQL + " where a.id = any(%s)", (list(ids),))
        cols = [d.name for d in cur.description]
        return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def code_verify(conn, text: str, assertion_ids: list[str]) -> dict:
    """assertion_ids may mix spine row IDs with literature IDs (pmid:..., up:...)."""
    spine_ids = [i for i in assertion_ids if not srcs.is_source_id(i)]
    rows = current_rows(conn, spine_ids)
    lit = srcs.load(conn, [i for i in assertion_ids if srcs.is_source_id(i)])
    lit_rows = [srcs.as_row(lit[i], text) for i in assertion_ids if i in lit]
    issues = []
    missing = [i for i in assertion_ids if i not in rows and i not in lit]
    if missing:
        issues.append("cited spine row(s) no longer exist: " + ", ".join(missing))
    stale = [f"{i} is {rows[i]['status']}" for i in spine_ids if i in rows and rows[i]["status"] != "accepted"]
    if stale:
        issues.append("cited spine row(s) not accepted: " + "; ".join(stale))
    if not assertion_ids:
        issues.append("cites no spine row")
    cited = [rows[i] for i in spine_ids if i in rows]
    res = steps.check_text(text, cited, lit_rows)
    issues += res["issues"]
    return {
        "number_checks": res["number_checks"],
        "code_issues": issues,
        "grades": [a["grade"] or "TODO(doctor)" for a in cited],
        "anchors": [{"assertion_id": a["id"], "anchor": api.anchor_text(a),
                     "statement": api.statement(a), "condition_text": a["condition_text"],
                     "doctor_checked": a["doctor_checked"], "status": a["status"]} for a in cited]
                   + [{"assertion_id": d["id"], "anchor": d["citation"], "statement": d["title"],
                       "condition_text": None, "doctor_checked": True, "status": "accepted",
                       "kind": d["kind"], "url": d["url"]} for d in lit_rows],
        "_rows": cited + lit_rows,
    }


def judge_support(llm: LLM, items: list[tuple[str, list[dict]]], *, run_id: str,
                  log_conn=None, step: str = "verifier") -> list[dict]:
    """items: (claim text, cited rows). Returns one {verdict, reason} per item, in order."""
    if not items:
        return []
    # Each source is listed once (an abstract cited by three claims is not sent three times);
    # each claim names the IDs of its own sources.
    unique: dict[str, dict] = {}
    for _, rows in items:
        for a in rows:
            unique.setdefault(a["id"], a)
    blocks = ["Sources:\n" + "\n".join(f"- {steps.assertion_line(a)}" for a in unique.values())]
    for n, (text, rows) in enumerate(items, 1):
        ids = ", ".join(a["id"] for a in rows) or "none"
        blocks.append(f"Claim {n} (its sources: {ids}): {text}")

    def check(out):
        got = sorted(v["claim"] for v in out["verdicts"])
        want = list(range(1, len(items) + 1))
        return [] if got == want else [f"give exactly one verdict for each claim number {want}"]

    out = call_logged(llm, SUPPORT_SYSTEM, "\n\n".join(blocks), SUPPORT_SCHEMA, step=step,
                      run_id=run_id, validate=check, log_conn=log_conn)
    by_n = {v["claim"]: v for v in out["verdicts"]}
    return [{"verdict": by_n[n]["verdict"], "reason": by_n[n]["reason"]} for n in range(1, len(items) + 1)]


def combine(code: dict, support: dict | None) -> dict:
    issues = list(code["code_issues"])
    if support is None:
        issues.append("support judge unavailable")
    elif support["verdict"] != "supported":
        issues.append(f"support judge: {support['verdict'].replace('_', ' ')} ({support['reason']})")
    out = {k: v for k, v in code.items() if not k.startswith("_")}
    out.update(support=support, issues=issues, ok=not issues)
    return out


def verify_texts(conn, llm: LLM, claims: list[dict], *, run_id: str, log_conn=None,
                 step: str = "verifier") -> list[dict]:
    """Verify claims [{text, assertion_ids}] -> one verdict dict per claim."""
    codes = [code_verify(conn, c["text"], c["assertion_ids"]) for c in claims]
    try:
        supports = judge_support(llm, [(c["text"], code["_rows"]) for c, code in zip(claims, codes)],
                                 run_id=run_id, log_conn=log_conn, step=step)
    except LLMError:
        supports = [None] * len(claims)
    return [combine(code, sup) for code, sup in zip(codes, supports)]


def verify_section(conn, llm: LLM, heading: str, audience: str, claims: list[dict],
                   by_id: dict[str, dict], *, run_id: str, log_conn=None, rewrite=None) -> list[dict]:
    """Verify a section's claims; rewrite each failing claim once; return final claims.
    rewrite(rows, feedback) -> claims; defaults to the spine-only Synthesizer."""
    verdicts = verify_texts(conn, llm, claims, run_id=run_id, log_conn=log_conn,
                            step=f"verifier:{heading}")
    out = []
    for c, v in zip(claims, verdicts):
        final = {"text": c["text"], "assertion_ids": c["assertion_ids"], "verdict": v,
                 "regenerated": False, "first_attempt": None}
        if not v["ok"]:
            rows = [by_id[i] for i in c["assertion_ids"] if i in by_id]
            try:
                feedback = f"Claim: {c['text']}\nProblems: " + "; ".join(v["issues"])
                redo = (rewrite(rows, feedback) if rewrite else
                        steps.synthesizer(llm, heading, audience, rows, run_id=run_id,
                                          log_conn=log_conn, feedback=feedback))
            except LLMError:
                redo = []
            if redo:
                new = {"text": " ".join(r["text"].strip() for r in redo),
                       "assertion_ids": list(dict.fromkeys(i for r in redo for i in r["assertion_ids"]))}
                nv = verify_texts(conn, llm, [new], run_id=run_id, log_conn=log_conn,
                                  step=f"verifier:{heading}:retry")[0]
                if nv["ok"] or len(nv["issues"]) < len(v["issues"]):
                    final = {**new, "verdict": nv, "regenerated": True,
                             "first_attempt": {"text": c["text"], "issues": v["issues"]}}
        out.append(final)
    return out
