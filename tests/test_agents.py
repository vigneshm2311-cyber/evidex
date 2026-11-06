"""Pipeline tests with a scripted fake model (no Ollama needed)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidex import pipeline  # noqa: E402
from evidex.agents import steps  # noqa: E402
from evidex.llm import LLMError  # noqa: E402
from evidex.spine import importer  # noqa: E402

CONCEPTS = """id,type,name,synonyms,notes
cond:diabetes,Condition,Diabetes mellitus,DM,
cond:prediabetes,Condition,Prediabetes,,
test:a1c,Test,Glycated haemoglobin (A1C),HbA1c,
test:fpg,Test,Fasting plasma glucose,FPG,
test:plasma_glucose,Test,Plasma glucose criteria,,
cond:sickle_cell_disease,Condition,Sickle cell disease,,
pop:general,Population,General,,
"""
H = ",".join(importer.ASSERTION_COLS)
ROWS = [
    "dx-a1c,cond:diabetes,diagnosed_by,test:a1c,>=,6.5,,%,pop:general,NGSP-certified method,,accepted,2,,,t,,false,",
    "dx-fpg,cond:diabetes,diagnosed_by,test:fpg,>=,126,,mg/dL,pop:general,Fasting for at least 8 h,B,accepted,2,Rec 2.x,S1,t,,false,",
    "pre-a1c,cond:prediabetes,diagnosed_by,test:a1c,range,5.7,6.4,%,pop:general,,,accepted,2,,,t,,false,",
    "a1c-sickle,test:a1c,unreliable_when,cond:sickle_cell_disease,,,,,pop:general,,,accepted,2,,,t,,false,",
]


class FakeLLM:
    """Returns scripted JSON per agent; records every call."""
    model = "fake"

    def __init__(self, scope=None, plan=None, claims=None):
        self.scope = scope or {"label": "in_scope_topic", "reason": "general", "general_query": "q"}
        self.plan = plan
        self.claims = claims or {}
        self.calls = []

    def chat_json(self, system, user, schema):
        self.calls.append((system, user))
        if system is steps.SCOPE_SYSTEM:
            self.last_reply = self.scope
            return self.scope
        if system is steps.PLANNER_SYSTEM:
            return self.plan(user) if callable(self.plan) else self.plan
        if system is steps.SYNTH_SYSTEM:
            heading = user.split("\n", 1)[0].removeprefix("Section: ")
            c = self.claims.get(heading, [])
            return {"claims": c(user) if callable(c) else c}
        raise AssertionError("unknown agent")


@pytest.fixture
def conn(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    url = os.environ.get("TEST_DATABASE_URL", "postgresql:///evidex_test")
    try:
        c = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("test database not available")
    c.execute("drop schema public cascade; create schema public;")
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        c.execute(f.read_text())
    (tmp_path / "concepts.csv").write_text(CONCEPTS)
    (tmp_path / "assertions.csv").write_text(H + "\n" + "\n".join(ROWS) + "\n")
    spine, _ = importer.load(tmp_path)
    importer.write(c, spine, "seed")
    yield c
    c.close()


def run(conn, llm, request="Diagnosing diabetes with HbA1c"):
    return list(pipeline.run(conn, llm, request, "slides", "Medical students"))


GOOD_PLAN = {"title": "Diagnosing diabetes", "not_covered": [],
             "sections": [{"heading": "Criteria", "assertion_ids": ["dx-a1c", "dx-fpg"]},
                          {"heading": "Caveats", "assertion_ids": ["a1c-sickle"]}]}
GOOD_CLAIMS = {
    "Criteria": [
        {"text": "ADA: diabetes is diagnosed at an A1C of 6.5% or higher.",
         "assertion_ids": ["dx-a1c"], "numbers": [{"value": 6.5, "unit": "%", "assertion_id": "dx-a1c"}]},
        {"text": "A fasting plasma glucose of 126 mg/dL or higher, after fasting at least 8 h.",
         "assertion_ids": ["dx-fpg"], "numbers": [{"value": 126, "unit": "mg/dL", "assertion_id": "dx-fpg"}]},
    ],
    "Caveats": [{"text": "A1C is unreliable in sickle cell disease.",
                 "assertion_ids": ["a1c-sickle"], "numbers": []}],
}


# ---- scope guard ----

def test_patient_dose_question_declined_by_model(conn):
    llm = FakeLLM(scope={"label": "out_of_scope", "reason": "dose for a patient",
                         "general_query": "Metformin in type 2 diabetes: what ADA says"})
    ev = run(conn, llm, "What metformin dose should I start for my patient?")
    assert [(e["step"], e["status"]) for e in ev] == [("scope_guard", "declined")]
    assert ev[0]["data"]["general_query"].startswith("Metformin")
    assert len(llm.calls) == 1  # nothing else ran


def test_code_rule_overrides_a_permissive_model(conn):
    ev = run(conn, FakeLLM(), "My patient is 54 years old with HbA1c 7.2, does he have diabetes?")
    assert ev[0]["status"] == "declined"
    assert ev[0]["data"]["decided_by"] == "code rule"
    assert "gives a patient's age" in ev[0]["data"]["reason"]


@pytest.mark.parametrize("text", [
    "Diagnostic criteria for diabetes", "When is HbA1c unreliable?",
    "Teaching slides on screening for diabetes in Indian adults"])
def test_general_topics_have_no_red_flags(text):
    assert steps.red_flags(text) == []


# ---- not found ----

def test_topic_outside_spine_is_not_found_without_planner(conn):
    llm = FakeLLM()
    ev = run(conn, llm, "Insulin pump settings")
    assert [(e["step"], e["status"]) for e in ev][-1] == ("spine_lookup", "not_found")
    assert ev[-1]["data"]["message"] == "Not found in sources"
    assert len(llm.calls) == 1  # planner never called


def test_planner_returning_nothing_is_not_found(conn):
    llm = FakeLLM(plan={"title": "x", "sections": [], "not_covered": ["smartphone apps"]})
    ev = run(conn, llm, "Smartphone apps for diagnosing diabetes")
    assert ev[-1]["step"] == "planner" and ev[-1]["status"] == "not_found"
    assert ev[-1]["data"]["not_covered"] == ["smartphone apps"]


# ---- happy path and number checks ----

def test_full_run_produces_checked_claims_with_spine_grades(conn):
    llm = FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS)
    ev = run(conn, llm)
    assert [e["status"] for e in ev] == ["ok", "ok", "ok", "ok", "ok", "ok"]
    done = ev[-1]["data"]
    crit = done["sections"][0]["claims"]
    assert all(c["ok"] for c in crit)
    assert crit[1]["grades"] == ["B"] and crit[0]["grades"] == ["TODO(doctor)"]
    assert "Rec 2.x" in crit[1]["anchors"][0]["anchor"]
    # planner saw only candidate rows; synthesizer saw only its section's rows
    synth_user = [u for s, u in llm.calls if s is steps.SYNTH_SYSTEM][1]
    assert "a1c-sickle" in synth_user and "dx-fpg" not in synth_user


def test_wrong_number_is_flagged_not_hidden(conn):
    bad = {**GOOD_CLAIMS, "Criteria": [
        {"text": "Diabetes is diagnosed at an A1C of 7.0% or higher.", "assertion_ids": ["dx-a1c"],
         "numbers": [{"value": 7.0, "unit": "%", "assertion_id": "dx-a1c"}]}]}
    ev = run(conn, FakeLLM(plan=GOOD_PLAN, claims=bad))
    crit = next(e for e in ev if e["step"] == "synthesizer:Criteria")
    assert crit["status"] == "flagged"
    c = crit["data"]["claims"][0]
    assert not c["ok"]
    assert any("spine says ≥ 6.5 %" in i for i in c["issues"])
    assert any("not in the cited spine rows: 7.0" in i for i in c["issues"])


def test_undeclared_number_and_wrong_unit_are_flagged(conn):
    by_id = {r["id"]: r for r in __import__("evidex.spine.api", fromlist=["x"]).all_assertions(conn)}
    c = steps.check_claim({"text": "FPG of 126 mmol/L, or 7.0 in some labs.", "assertion_ids": ["dx-fpg"],
                           "numbers": [{"value": 126, "unit": "mmol/L", "assertion_id": "dx-fpg"}]}, by_id)
    assert not c["ok"]
    assert any("mmol/L" in i for i in c["issues"])
    assert any("7.0" in i for i in c["issues"])


def test_range_ends_and_qualifier_numbers_are_allowed(conn):
    by_id = {r["id"]: r for r in __import__("evidex.spine.api", fromlist=["x"]).all_assertions(conn)}
    c = steps.check_claim({"text": "Prediabetes: A1C 5.7-6.4%.", "assertion_ids": ["pre-a1c"],
                           "numbers": [{"value": 5.7, "unit": "%", "assertion_id": "pre-a1c"},
                                       {"value": 6.4, "unit": "%", "assertion_id": "pre-a1c"}]}, by_id)
    assert c["ok"], c["issues"]


def test_patient_directed_wording_is_flagged(conn):
    by_id = {r["id"]: r for r in __import__("evidex.spine.api", fromlist=["x"]).all_assertions(conn)}
    c = steps.check_claim({"text": "Your patient should have an A1C test.", "assertion_ids": ["dx-a1c"],
                           "numbers": []}, by_id)
    assert "patient-directed wording" in c["issues"]


# ---- retries and audit log ----

def test_planner_unknown_id_retries_then_succeeds(conn):
    answers = iter([{"title": "t", "not_covered": [], "sections": [{"heading": "A", "assertion_ids": ["made-up"]}]},
                    GOOD_PLAN])
    llm = FakeLLM(plan=lambda user: next(answers), claims=GOOD_CLAIMS)
    ev = run(conn, llm)
    assert ev[2]["status"] == "ok"
    row = conn.execute("select attempts, error from agent_call where step = 'planner'").fetchone()
    assert row == (2, None)


def test_every_agent_call_is_logged_with_its_input(conn):
    ev = run(conn, FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS))
    rows = conn.execute("select step, input, output from agent_call where run_id = %s order by id",
                        (ev[0]["run_id"],)).fetchall()
    assert [r[0] for r in rows] == ["scope_guard", "planner", "synthesizer:Criteria", "synthesizer:Caveats"]
    assert "Diagnosing diabetes with HbA1c" in rows[0][1]["user"]
    assert rows[1][2]["title"] == "Diagnosing diabetes"


def test_model_failure_is_reported_not_guessed(conn):
    class Broken(FakeLLM):
        def chat_json(self, system, user, schema):
            raise LLMError("cannot reach Ollama")
    ev = run(conn, Broken())
    assert ev == [ev[0]] and ev[0]["status"] == "error"
    assert conn.execute("select attempts, error from agent_call").fetchone() == (2, "cannot reach Ollama")


def test_planner_padding_is_replaced_by_not_found(conn):
    padded = {"title": "Apps", "not_covered": ["Smartphone apps for diagnosing diabetes", "app stores"],
              "sections": [{"heading": "Background", "assertion_ids": ["dx-a1c"]}]}
    ev = run(conn, FakeLLM(plan=padded), "Smartphone apps for diagnosing diabetes")
    assert (ev[-1]["step"], ev[-1]["status"]) == ("planner", "not_found")
    assert ev[-1]["data"]["main_subject_missing"]


def test_partial_gaps_do_not_block_the_outline():
    assert not steps.same_subject("Diagnosis in children", "Diagnosing diabetes in Indian adults")
    assert steps.same_subject("Insulin pump settings for type 1 diabetes",
                              "Teaching slides on insulin pump settings for type 1 diabetes")


def test_general_query_with_patient_details_is_dropped(conn):
    llm = FakeLLM(scope={"label": "out_of_scope", "reason": "dose",
                         "general_query": "What metformin dose for a patient with A1C 7.2?"})
    ev = run(conn, llm, "What metformin dose should I start for my patient with A1C 7.2?")
    assert ev[0]["status"] == "declined"
    assert ev[0]["data"]["general_query"] == ""
    assert len(llm.calls) == 2  # one retry asked for a cleaner suggestion


def test_clean_general_query_is_kept(conn):
    llm = FakeLLM(scope={"label": "out_of_scope", "reason": "dose",
                         "general_query": "Metformin in type 2 diabetes: what ADA says"})
    ev = run(conn, llm, "What metformin dose should I start for my patient with A1C 7.2?")
    assert ev[0]["data"]["general_query"] == "Metformin in type 2 diabetes: what ADA says"
    assert len(llm.calls) == 1


def test_listed_numbers_absent_from_text_are_ignored(conn):
    by_id = {r["id"]: r for r in __import__("evidex.spine.api", fromlist=["x"]).all_assertions(conn)}
    c = steps.check_claim({"text": "A1C is unreliable in sickle cell disease.", "assertion_ids": ["a1c-sickle"],
                           "numbers": [{"value": 6.5, "unit": "%", "assertion_id": "dx-a1c"}]}, by_id)
    assert c["ok"], c["issues"]


@pytest.mark.parametrize("text", [
    "What is the recommended starting dose of metformin for adults with type 2 diabetes?",
    "Insulin titration in type 2 diabetes", "Management of type 2 diabetes in India",
    "Insulin pump settings", "Glycaemic targets in older adults"])
def test_dosing_and_treatment_topics_are_not_found_before_any_model(conn, text):
    llm = FakeLLM()
    ev = run(conn, llm, text)
    assert (ev[-1]["step"], ev[-1]["status"]) == ("spine_lookup", "not_found")
    assert "covers only diagnosis, classification, screening and prevention" in ev[-1]["data"]["not_covered"][0]
    assert len(llm.calls) == 1  # only the scope guard ran


@pytest.mark.parametrize("text", [
    "Diagnosing diabetes in Indian adults", "When is HbA1c unreliable?",
    "Metformin for prevention of type 2 diabetes", "Screening for diabetes in pregnancy"])
def test_spine_topics_are_not_blocked(text):
    assert pipeline.out_of_spine(text) == []


def test_duplicate_claims_are_removed(conn):
    near_copy = {**GOOD_CLAIMS["Criteria"][0],
                 "text": "ADA: diabetes is diagnosed at an A1C of 6.5% or more."}
    dup = {"Criteria": GOOD_CLAIMS["Criteria"] + [near_copy], "Caveats": GOOD_CLAIMS["Caveats"]}
    ev = run(conn, FakeLLM(plan=GOOD_PLAN, claims=dup))
    texts = [c["text"] for s in ev[-1]["data"]["sections"] for c in s["claims"]]
    assert len(texts) == len(set(texts)) == 3


def test_full_outline_with_a_noted_gap_is_kept(conn):
    plan = {"title": "Dx", "not_covered": ["Diabetes in Indian adults: ethnic and dietary factors"],
            "sections": [{"heading": "Criteria", "assertion_ids": ["dx-a1c", "dx-fpg", "pre-a1c"]},
                         {"heading": "Caveats", "assertion_ids": ["a1c-sickle"]}]}
    ev = run(conn, FakeLLM(plan=plan, claims=GOOD_CLAIMS), "Diagnosing diabetes in Indian adults")
    assert ev[-1]["step"] == "done"
