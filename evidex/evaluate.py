"""Evaluation: automatic metrics, a number-error injection test, and gold-labelled metrics.

Gold labels (scope, answerable, must-cite) come only from the doctor's sheet. Metrics whose
labels are missing are reported as not computed, never estimated.
"""
from __future__ import annotations

import csv
import json
import re
import time
from decimal import Decimal
from pathlib import Path

from . import export, pipeline
from .agents import steps, verifier
from .llm import LLMError

GOLD_COLS = ["gold_scope", "gold_answerable", "gold_must_cite", "labelled_by"]


# ---------------- questions ----------------

def load_questions(csv_path: Path, jsonl_path: Path | None = None) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
    qs = []
    for r in rows:
        if not r.get("id") or not r.get("request"):
            continue
        scope = r.get("gold_scope", "").lower().replace("-", "_") or None
        ans = r.get("gold_answerable", "").lower()
        qs.append({
            "id": r["id"], "request": r["request"],
            "audience": r.get("audience") or steps.AUDIENCES[0],
            "artifact": r.get("artifact") or "slides",
            "drafted_as": r.get("drafted_as", ""),
            "gold": {
                "scope": scope if scope in ("in_scope", "out_of_scope") else None,
                "answerable": {"yes": True, "no": False}.get(ans),
                "must_cite": [x.strip() for x in r.get("gold_must_cite", "").split(";") if x.strip()],
                "labelled_by": r.get("labelled_by") or None,
            },
        })
    if jsonl_path:
        jsonl_path.write_text("".join(json.dumps(q) + "\n" for q in qs))
    return qs


# ---------------- running ----------------

def run_question(conn, llm, q: dict) -> dict:
    t0 = time.monotonic()
    events = list(pipeline.run(conn, llm, q["request"], q["artifact"], q["audience"],
                               save=False, run_prefix="eval-"))
    last = events[-1]
    outcome = {"declined": "declined", "not_found": "not_found", "error": "error"}.get(last["status"])
    if last["step"] == "done":
        outcome = "draft"
    claims = []
    sections = []
    if outcome == "draft":
        for s in last["data"]["sections"]:
            sections.append({"heading": s["heading"], "claims": s["claims"]})
            claims += s["claims"]
    guard = events[0]["data"] if events[0]["step"] == "scope_guard" else {}
    return {
        "id": q["id"], "request": q["request"], "run_id": last["run_id"], "outcome": outcome,
        "decided_by": guard.get("decided_by"), "general_query": guard.get("general_query"),
        "steps": [{"step": e["step"], "status": e["status"], "ms": e["ms"]} for e in events],
        "total_s": round(time.monotonic() - t0, 1),
        "title": last["data"].get("title") if outcome == "draft" else None,
        "sections": sections, "claims": claims,
        "error": last["data"].get("error") if outcome == "error" else None,
    }


# ---------------- A. automatic metrics ----------------

def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


def auto_metrics(results: list[dict]) -> dict:
    drafts = [r for r in results if r["outcome"] == "draft"]
    claims = [c for r in drafts for c in r["claims"]]
    cited = sum(1 for c in claims if not any("no longer exist" in i or "not accepted" in i or "cites no" in i
                                             for i in c["verdict"]["issues"]))
    nums = [n for c in claims for n in c["verdict"].get("number_checks", [])]
    first_pass = sum(1 for c in claims if c["verdict"]["ok"] and not c.get("regenerated"))
    after_retry = sum(1 for c in claims if c["verdict"]["ok"] and c.get("regenerated"))
    flagged = sum(1 for c in claims if not c["verdict"]["ok"])
    judge = [c["verdict"].get("support") for c in claims]
    times = sorted(r["total_s"] for r in drafts)
    return {
        "questions_run": len(results), "drafts": len(drafts), "claims": len(claims),
        "outcomes": {k: sum(1 for r in results if r["outcome"] == k)
                     for k in ("draft", "declined", "not_found", "error")},
        "citation_valid_pct": _pct(cited, len(claims)), "citation_valid": [cited, len(claims)],
        "numbers_checked": len(nums), "number_accuracy_pct": _pct(sum(n["ok"] for n in nums), len(nums)),
        "number_accuracy": [sum(n["ok"] for n in nums), len(nums)],
        "verifier_first_pass_pct": _pct(first_pass, len(claims)),
        "verifier_passed_after_rewrite": after_retry, "verifier_still_flagged": flagged,
        "judge_supported_pct": _pct(sum(1 for j in judge if j and j["verdict"] == "supported"), len(claims)),
        "draft_time_s": {"median": times[len(times) // 2] if times else None,
                         "min": times[0] if times else None, "max": times[-1] if times else None},
    }


def export_fidelity(results: list[dict]) -> dict:
    checked, passed, problems = 0, 0, []
    for r in results:
        if r["outcome"] != "draft":
            continue
        secs = [{"heading": s["heading"],
                 "claims": [{**c, "approved": c["verdict"]["ok"]} for c in s["claims"]]}
                for s in r["sections"]]
        if not any(c["approved"] for s in secs for c in s["claims"]):
            continue
        doc = export.build_unsaved(r["title"] or r["request"], "evaluation", secs, "evaluation run")
        checked += 1
        p = export.fidelity(doc)
        if not p:
            p = export.check_rendered(doc, export.pptx_text(export.to_pptx(doc)))
        if p:
            problems.append({"id": r["id"], "problems": p})
        else:
            passed += 1
    return {"drafts_exported": checked, "fidelity_passed": passed, "problems": problems}


# ---------------- B. number-error injection ----------------

def _perturb(value: Decimal, unit: str, forbidden: set[Decimal]) -> Decimal:
    u = (unit or "").lower()
    steps_ = ([Decimal("-0.5"), Decimal("0.3"), Decimal("-0.3"), Decimal("0.5")] if u.startswith("%")
              else [Decimal("-6"), Decimal("6"), Decimal("-10"), Decimal("10")] if "mg" in u
              else [Decimal("5"), Decimal("-5"), Decimal("2")] if u.startswith(("year", "month"))
              else [Decimal("1"), Decimal("-1"), Decimal("2")])
    for d in steps_:
        v = value + d
        if v > 0 and v not in forbidden:
            return v
    return value + 7


def make_injections(conn, results: list[dict], limit: int = 40) -> list[dict]:
    """One wrong number per passing claim that contains a spine-checked number."""
    out = []
    for r in results:
        for c in r["claims"]:
            if not c["verdict"]["ok"]:
                continue
            good = [n for n in c["verdict"].get("number_checks", []) if n["ok"]]
            if not good:
                continue
            n = good[0]
            rows = verifier.current_rows(conn, c["assertion_ids"])
            forbidden = set()
            for a in rows.values():
                forbidden |= steps.numbers_in(" ".join(filter(None, [
                    steps.api.statement(a), a.get("condition_text") or ""])))
                forbidden |= {Decimal(v) for v in (a["value"], a["value_high"]) if v is not None}
            orig = Decimal(n["value"])
            new = _perturb(orig, n["unit"], forbidden)
            pat = r"(?<![\w.])" + re.escape(n["value"]) + r"(?![\d])"
            text, k = re.subn(pat, format(new.normalize(), "f"), c["text"], count=1)
            if k:
                out.append({"question": r["id"], "original": c["text"], "text": text,
                            "assertion_ids": c["assertion_ids"], "from": n["value"],
                            "to": format(new.normalize(), "f"), "unit": n["unit"]})
            if len(out) >= limit:
                return out
    return out


def injection_test(conn, llm, injections: list[dict], batch: int = 8) -> dict:
    code_caught = 0
    for inj in injections:
        v = verifier.code_verify(conn, inj["text"], inj["assertion_ids"])
        inj["code_caught"] = bool(v["code_issues"])
        code_caught += inj["code_caught"]
    judge_caught, judged = 0, 0
    for i in range(0, len(injections), batch):
        chunk = injections[i:i + batch]
        items = [(x["text"], list(verifier.current_rows(conn, x["assertion_ids"]).values())) for x in chunk]
        try:
            verdicts = verifier.judge_support(llm, items, run_id="eval-injection", log_conn=conn,
                                              step="eval:injection_judge")
        except LLMError:
            continue
        for x, v in zip(chunk, verdicts):
            x["judge_verdict"] = v["verdict"]
            x["judge_caught"] = v["verdict"] != "supported"
            judge_caught += x["judge_caught"]
            judged += 1
    return {"injected": len(injections), "code_caught": code_caught,
            "code_caught_pct": _pct(code_caught, len(injections)),
            "judge_judged": judged, "judge_caught": judge_caught,
            "judge_caught_pct": _pct(judge_caught, judged), "cases": injections}


# ---------------- C. gold-labelled metrics ----------------

def gold_metrics(questions: list[dict], results: list[dict]) -> dict:
    by_id = {r["id"]: r for r in results}
    scoped = [(q, by_id[q["id"]]) for q in questions if q["gold"]["scope"] and q["id"] in by_id]
    out_gold = [(q, r) for q, r in scoped if q["gold"]["scope"] == "out_of_scope"]
    in_gold = [(q, r) for q, r in scoped if q["gold"]["scope"] == "in_scope"]
    blocked = sum(1 for _, r in out_gold if r["outcome"] == "declined")
    false_ref = sum(1 for _, r in in_gold if r["outcome"] == "declined")

    ans = [(q, by_id[q["id"]]) for q in questions
           if q["gold"]["scope"] == "in_scope" and q["gold"]["answerable"] is not None and q["id"] in by_id
           and by_id[q["id"]]["outcome"] != "declined"]
    nf_correct = sum(1 for q, r in ans if (r["outcome"] == "not_found") == (q["gold"]["answerable"] is False))
    unanswerable = [(q, r) for q, r in ans if q["gold"]["answerable"] is False]
    answerable = [(q, r) for q, r in ans if q["gold"]["answerable"] is True]

    cite = []
    for q in questions:
        r = by_id.get(q["id"])
        if q["gold"]["must_cite"] and r and r["outcome"] == "draft":
            got = {i for c in r["claims"] for i in c["assertion_ids"]}
            cite.append(len(set(q["gold"]["must_cite"]) & got) / len(q["gold"]["must_cite"]))

    unlabelled = [q["id"] for q in questions if not q["gold"]["scope"]]
    return {
        "labelled": len(scoped), "unlabelled": unlabelled,
        "labelled_by": sorted({q["gold"]["labelled_by"] for q in questions if q["gold"]["labelled_by"]}),
        "scope_block_rate_pct": _pct(blocked, len(out_gold)), "scope_block": [blocked, len(out_gold)],
        "false_refusal_pct": _pct(false_ref, len(in_gold)), "false_refusal": [false_ref, len(in_gold)],
        "not_found_accuracy_pct": _pct(nf_correct, len(ans)), "not_found_accuracy": [nf_correct, len(ans)],
        "not_found_on_unanswerable": [sum(r["outcome"] == "not_found" for _, r in unanswerable), len(unanswerable)],
        "drafted_on_answerable": [sum(r["outcome"] == "draft" for _, r in answerable), len(answerable)],
        "must_cite_recall_pct": round(100 * sum(cite) / len(cite), 1) if cite else None,
        "must_cite_n": len(cite),
        "errors": [r["id"] for r in results if r["outcome"] == "error"],
    }


# ---------------- report ----------------

def _fmt(pct, pair=None):
    if pct is None:
        return "not computed (no data)"
    return f"**{pct}%**" + (f" ({pair[0]}/{pair[1]})" if pair else "")


def report_md(meta: dict, results: list[dict], auto: dict | None, fid: dict | None,
              inj: dict | None, gold: dict | None) -> str:
    L = [f"# Evidex evaluation report", "",
         f"Run {meta['started']} · model `{meta['model']}` · {meta['questions']} questions · "
         f"spine: {meta['spine_rows']} accepted rows, {meta['spine_checked']} checked by the doctor against the 2026 text.",
         "", "All numbers below were produced by `scripts/eval.py` in this run; nothing is estimated.", ""]
    L += ["## Outcomes per question", "", "| id | request | outcome | time (s) | claims | flagged |", "|---|---|---|---|---|---|"]
    for r in results:
        fl = sum(1 for c in r["claims"] if not c["verdict"]["ok"])
        L.append(f"| {r['id']} | {r['request'][:70]} | {r['outcome']}"
                 + (f" ({r['decided_by']})" if r["outcome"] == "declined" else "")
                 + f" | {r['total_s']} | {len(r['claims'])} | {fl} |")
    if auto:
        L += ["", "## A. Automatic metrics (reference: the spine)", "",
              f"- Drafts produced: {auto['drafts']} with {auto['claims']} claims",
              f"- Citation validity (cited rows exist and are accepted): {_fmt(auto['citation_valid_pct'], auto['citation_valid'])}",
              f"- Number accuracy (numbers in claims equal the cited spine value and unit): {_fmt(auto['number_accuracy_pct'], auto['number_accuracy'])}",
              f"- Verifier: passed first time {_fmt(auto['verifier_first_pass_pct'])}; passed after one rewrite: {auto['verifier_passed_after_rewrite']}; still flagged for the doctor: {auto['verifier_still_flagged']}",
              f"- Support judge said 'supported' on final claims: {_fmt(auto['judge_supported_pct'])}",
              f"- Time per draft: median {auto['draft_time_s']['median']} s (range {auto['draft_time_s']['min']}–{auto['draft_time_s']['max']} s)"]
    if fid:
        L += [f"- Export fidelity (verifier-passing claims exported to PPTX, every sentence matched): "
              f"**{fid['fidelity_passed']}/{fid['drafts_exported']}** drafts"]
    if inj:
        L += ["", "## B. Number-error injection", "",
              f"One number in each passing claim was changed by code (e.g. {inj['cases'][0]['from']} → {inj['cases'][0]['to']} {inj['cases'][0]['unit']})." if inj["cases"] else "No claims with numbers were available.",
              f"- Code Verifier caught: {_fmt(inj['code_caught_pct'], [inj['code_caught'], inj['injected']])}",
              f"- LLM support judge alone caught: {_fmt(inj['judge_caught_pct'], [inj['judge_caught'], inj['judge_judged']])}"]
    if gold:
        L += ["", "## C. Gold-labelled metrics (labels by the doctor)", ""]
        if not gold["labelled"]:
            L += [f"Not computed: none of the {len(gold['unlabelled'])} questions has a gold label yet "
                  "(`eval/questions.csv`)."]
        else:
            L += [f"Labelled questions: {gold['labelled']} (by {', '.join(gold['labelled_by']) or 'unnamed'}); "
                  f"unlabelled: {len(gold['unlabelled'])}.",
                  f"- Scope Guard block rate (patient-specific requests declined): {_fmt(gold['scope_block_rate_pct'], gold['scope_block'])}",
                  f"- False-refusal rate (general requests declined): {_fmt(gold['false_refusal_pct'], gold['false_refusal'])}",
                  f"- 'Not found' accuracy (in-scope requests): {_fmt(gold['not_found_accuracy_pct'], gold['not_found_accuracy'])}"
                  f"; said Not found on {gold['not_found_on_unanswerable'][0]}/{gold['not_found_on_unanswerable'][1]} unanswerable,"
                  f" drafted on {gold['drafted_on_answerable'][0]}/{gold['drafted_on_answerable'][1]} answerable",
                  f"- Must-cite recall: {_fmt(gold['must_cite_recall_pct'])} over {gold['must_cite_n']} question(s)"]
    errs = [r["id"] for r in results if r["outcome"] == "error"]
    L += ["", "## Limits", "",
          "- Small question set drafted by the team; gold labels are the doctor's own.",
          "- Spine values not yet fully checked against the 2026 text (see header); metrics measure agreement with the spine, not clinical truth.",
          "- One local 8B model; results may differ with other models or runs.",
          f"- Errors during the run: {', '.join(errs) if errs else 'none'}."]
    return "\n".join(L) + "\n"
