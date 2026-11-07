"""Verifier and doctor-review rules (fake model, local test database)."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_agents import GOOD_CLAIMS, GOOD_PLAN, FakeLLM, conn, run  # noqa: E402,F401

from evidex import drafts  # noqa: E402
from evidex.agents import verifier  # noqa: E402


def make_draft(conn, llm=None):
    ev = run(conn, llm or FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS))
    return drafts.load(conn, ev[-1]["data"]["draft_id"])


def claim_with(d, word):
    return next(c for c in d["claims"] if word in c["text"])


# ---- verifier ----

def test_support_judge_partly_flags_and_one_rewrite_fixes(conn):
    calls = {"n": 0}

    def support(user):
        calls["n"] += 1
        n = len(re.findall(r"(?m)^Claim \d+ ", user))
        # first judgement of the Criteria section: claim 1 overreaches; everything else fine
        if calls["n"] == 1:
            return [{"claim": 1, "verdict": "partly", "reason": "adds 'only test needed'"}] + [
                {"claim": i, "verdict": "supported", "reason": "ok"} for i in range(2, n + 1)]
        return [{"claim": i, "verdict": "supported", "reason": "ok"} for i in range(1, n + 1)]

    def criteria(user):
        if "A checker rejected" in user:
            return [{"text": "Diabetes is diagnosed at an A1C of 6.5% or higher.", "assertion_ids": ["dx-a1c"],
                     "numbers": []}]
        return GOOD_CLAIMS["Criteria"]

    llm = FakeLLM(plan=GOOD_PLAN, claims={**GOOD_CLAIMS, "Criteria": criteria}, support=support)
    d = make_draft(conn, llm)
    c = d["sections"][0]["claims"][0]
    assert c["verdict"]["ok"] and c["verdict"]["regenerated"]
    assert "only test needed" in " ".join(c["verdict"]["first_attempt"]["issues"])


def test_claim_still_failing_after_retry_stays_flagged(conn):
    def support(user):
        n = len(re.findall(r"(?m)^Claim \d+ ", user))
        return [{"claim": i, "verdict": "not_supported", "reason": "no"} for i in range(1, n + 1)]
    d = make_draft(conn, FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS, support=support))
    assert all(not c["verdict"]["ok"] for c in d["claims"])
    assert all(any("support judge: not supported" in i for i in c["verdict"]["issues"]) for c in d["claims"])


def test_verifier_uses_current_spine_status(conn):
    conn.execute("update assertion set status = 'superseded' where id = 'dx-a1c'")
    v = verifier.code_verify(conn, "A1C of 6.5% or higher.", ["dx-a1c"])
    assert any("dx-a1c is superseded" in i for i in v["code_issues"])
    v = verifier.code_verify(conn, "text", ["gone"])
    assert any("no longer exist: gone" in i for i in v["code_issues"])


# ---- doctor review ----

def test_edit_to_wrong_number_is_flagged_and_cannot_be_approved(conn):
    llm = FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS)
    d = make_draft(conn, llm)
    c = claim_with(d, "6.5%")
    c = drafts.edit(conn, llm, c["id"], "Dr Test", "ADA: diabetes is diagnosed at an A1C of 6.0% or higher.")
    assert not c["verdict"]["ok"] and c["status"] == "pending"
    assert any("says 6.0 %; spine says ≥ 6.5 % (dx-a1c)" in i for i in c["verdict"]["issues"])
    with pytest.raises(drafts.ReviewError, match="cannot approve a flagged claim"):
        drafts.decide(conn, c["id"], "Dr Test", "approved", numbers_checked=True)
    assert c["original_text"].endswith("6.5% or higher.")


def test_propose_correction_creates_proposed_row_and_leaves_spine_alone(conn):
    llm = FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS)
    d = make_draft(conn, llm)
    c = drafts.edit(conn, llm, claim_with(d, "6.5%")["id"], "Dr Test", "Diabetes at an A1C of 6.0% or higher.")
    bad = next(n for n in c["verdict"]["number_checks"] if not n["ok"])
    new_id = drafts.propose_correction(conn, c["id"], "Dr Test", bad)
    assert new_id == "dx-a1c-prop-1"
    rows = dict(conn.execute("select id, status from assertion where id like 'dx-a1c%'").fetchall())
    assert rows == {"dx-a1c": "accepted", "dx-a1c-prop-1": "proposed"}
    assert float(conn.execute("select value from assertion where id = 'dx-a1c'").fetchone()[0]) == 6.5
    assert conn.execute("select actor from change_log where record_id = 'dx-a1c-prop-1'").fetchone() == ("Dr Test",)


def test_approval_needs_numbers_ticked_and_a_name(conn):
    d = make_draft(conn)
    c = claim_with(d, "6.5%")
    with pytest.raises(drafts.ReviewError, match="numbers checked"):
        drafts.decide(conn, c["id"], "Dr Test", "approved")
    with pytest.raises(drafts.ReviewError, match="enter your name"):
        drafts.decide(conn, c["id"], " ", "approved", numbers_checked=True)
    c = drafts.decide(conn, c["id"], "Dr Test", "approved", numbers_checked=True)
    assert (c["status"], c["reviewed_by"]) == ("approved", "Dr Test")
    s = claim_with(d, "sickle")  # no numbers: no tick needed
    assert drafts.decide(conn, s["id"], "Dr Test", "approved")["status"] == "approved"


def test_sign_off_gate_lock_and_audit(conn):
    llm = FakeLLM(plan=GOOD_PLAN, claims=GOOD_CLAIMS)
    d = make_draft(conn, llm)
    st = drafts.signoff_state(d)
    assert not st["can_sign"] and "3 claim(s) still to approve or reject" in st["reasons"]
    with pytest.raises(drafts.ReviewError, match="cannot sign off"):
        drafts.sign_off(conn, d["id"], "Dr Test")

    for c in d["claims"]:
        if "126" in c["text"]:
            drafts.decide(conn, c["id"], "Dr Test", "rejected")
        else:
            drafts.decide(conn, c["id"], "Dr Test", "approved", numbers_checked=True)
    d = drafts.load(conn, d["id"])
    st = drafts.signoff_state(d)
    assert st["can_sign"] and (st["approved"], st["rejected"]) == (2, 1)
    assert st["unchecked_rows"] == ["a1c-sickle", "dx-a1c"]  # rows not yet checked against 2026 text

    d = drafts.sign_off(conn, d["id"], "Dr Test")
    assert (d["status"], d["signed_by"]) == ("signed_off", "Dr Test")
    with pytest.raises(drafts.ReviewError, match="locked"):
        drafts.edit(conn, llm, d["claims"][0]["id"], "Dr Test", "changed")
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="locked"):
        conn.execute("update claim set text = 'x' where draft_id = %s", (d["id"],))
    log = conn.execute("select record_table, action, actor from change_log where record_table in ('claim', 'draft') "
                       "order by id").fetchall()
    assert ("draft", "in_review->signed_off", "Dr Test") in log
    assert sum(1 for t, a, _ in log if t == "claim" and a == "pending->approved") == 2
