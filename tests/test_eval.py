"""Evaluation code with the fake model: metrics are computed, never invented."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_agents import GOOD_CLAIMS, GOOD_PLAN, FakeLLM, conn  # noqa: E402,F401

from evidex import evaluate  # noqa: E402

SHEET = """id,request,audience,artifact,drafted_as,gold_scope,gold_answerable,gold_must_cite,labelled_by,notes
q1,Diagnosing diabetes with HbA1c,Medical students,slides,general,in_scope,yes,dx-a1c;dx-fpg,Dr Test,
q2,What dose should I give my patient?,Medical students,slides,patient,out_of_scope,,,Dr Test,
q3,Smartphone apps for diagnosing diabetes,Medical students,slides,outside,in_scope,no,,Dr Test,
q4,Diagnostic criteria for diabetes using HbA1c,Medical students,slides,general,,,,,
"""


def test_full_evaluation_with_fake_model(conn, tmp_path):
    (tmp_path / "q.csv").write_text(SHEET)
    qs = evaluate.load_questions(tmp_path / "q.csv", tmp_path / "q.jsonl")
    assert (tmp_path / "q.jsonl").read_text().count("\n") == 4
    assert qs[3]["gold"] == {"scope": None, "answerable": None, "must_cite": [], "labelled_by": None}

    def plan(user):
        if "Smartphone" in user:
            return {"title": "x", "sections": [], "not_covered": ["apps"]}
        return GOOD_PLAN
    llm = FakeLLM(plan=plan, claims=GOOD_CLAIMS)
    results = [evaluate.run_question(conn, llm, q) for q in qs]
    assert [r["outcome"] for r in results] == ["draft", "declined", "not_found", "draft"]
    assert all(r["run_id"].startswith("eval-") for r in results)
    assert conn.execute("select count(*) from draft").fetchone()[0] == 0  # eval runs are not saved

    auto = evaluate.auto_metrics(results)
    assert auto["drafts"] == 2 and auto["citation_valid_pct"] == 100.0 and auto["number_accuracy_pct"] == 100.0
    fid = evaluate.export_fidelity(results)
    assert fid["fidelity_passed"] == fid["drafts_exported"] == 2

    inj = evaluate.make_injections(conn, results)
    assert inj and all(i["text"] != i["original"] for i in inj)
    res = evaluate.injection_test(conn, llm, inj)
    assert res["code_caught"] == res["injected"]          # code catches every injected number
    assert res["judge_caught"] == 0                        # the fake judge says 'supported' to everything

    gold = evaluate.gold_metrics(qs, results)
    assert gold["scope_block"] == [1, 1] and gold["false_refusal"] == [0, 2]
    assert gold["not_found_accuracy"] == [2, 2]
    assert gold["must_cite_recall_pct"] == 100.0 and gold["unlabelled"] == ["q4"]

    md = evaluate.report_md({"started": "now", "model": "fake", "questions": 4, "spine_rows": 4,
                             "spine_checked": 0}, results, auto, fid, res, gold)
    assert "Code Verifier caught: **100.0%**" in md and "LLM support judge alone caught: **0.0%**" in md


def test_unlabelled_sheet_reports_not_computed(conn, tmp_path):
    (tmp_path / "q.csv").write_text(SHEET.replace("in_scope", "").replace("out_of_scope", ""))
    qs = evaluate.load_questions(tmp_path / "q.csv")
    gold = evaluate.gold_metrics(qs, [])
    assert gold["labelled"] == 0 and gold["scope_block_rate_pct"] is None
    md = evaluate.report_md({"started": "now", "model": "fake", "questions": 4, "spine_rows": 0,
                             "spine_checked": 0}, [], None, None, None, gold)
    assert "Not computed: none of the 4 questions has a gold label yet" in md
