"""Export tests: only signed-off drafts, exact approved text, fidelity blocks, audit entry."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from test_agents import conn  # noqa: E402,F401
from test_review import make_draft  # noqa: E402

from evidex import drafts, export  # noqa: E402


def signed_draft(conn, reject_word="126", reject_all_in=None):
    d = make_draft(conn)
    for c in d["claims"]:
        if reject_word in c["text"] or (reject_all_in and c["section"] == reject_all_in):
            drafts.decide(conn, c["id"], "Dr Test", "rejected")
        else:
            drafts.decide(conn, c["id"], "Dr Test", "approved", numbers_checked=True)
    return drafts.sign_off(conn, d["id"], "Dr Test")


def test_unsigned_draft_cannot_be_exported(conn):
    d = make_draft(conn)
    with pytest.raises(export.ExportError, match="only signed-off drafts"):
        export.render(conn, d["id"], "pptx")


@pytest.mark.parametrize("fmt", ["pptx", "docx"])
def test_export_contains_exactly_the_approved_text(conn, fmt):
    d = signed_draft(conn, reject_word="sickle")
    out = export.render(conn, d["id"], fmt)
    text = (export.pptx_text if fmt == "pptx" else export.docx_text)(out["data"])
    approved = [c["text"] for c in d["claims"] if c["status"] == "approved"]
    rejected = [c["text"] for c in d["claims"] if c["status"] == "rejected"]
    assert approved and rejected
    for t in approved:
        assert t in text
    for t in rejected:
        assert t not in text
    assert "References" in text and "Rec 2.x" in text          # anchor from the spine
    assert "Rec/Table TODO(doctor)" in text                    # missing anchors shown honestly
    assert "Not yet checked by the doctor against the 2026 text" in text
    assert "approved by Dr Test" in text and "fake" in text    # AI-use note names the model
    assert out["filename"] == f"diagnosing-diabetes.{fmt}" and len(out["sha256"]) == 64


def test_fully_rejected_section_says_not_provided(conn):
    d = signed_draft(conn, reject_word="@@none@@", reject_all_in="Caveats")
    text = export.pptx_text(export.render(conn, d["id"], "pptx")["data"])
    assert "Caveats" in text and export.NOT_PROVIDED in text


def test_fidelity_blocks_tampered_documents(conn):
    d = signed_draft(conn)
    doc = export.build(conn, d["id"])
    assert export.fidelity(doc) == []
    doc["sections"][0]["items"][0]["text"] = "Diabetes is diagnosed at an A1C of 7.0%."
    problems = export.fidelity(doc)
    assert any("not an approved claim" in p for p in problems)
    assert any("number(s) not in approved claims: 7.0" in p for p in problems)
    doc = export.build(conn, d["id"])
    doc["sections"][0]["items"].append({**doc["sections"][0]["items"][0], "text": doc["other_texts"][0]})
    assert any("rejected or pending claim included" in p for p in export.fidelity(doc))


def test_export_is_logged_with_checksum(conn):
    d = signed_draft(conn)
    out = export.render(conn, d["id"], "docx")
    export.log_export(conn, d["id"], "docx", "Dr Test", out["sha256"], out["claims"])
    row = conn.execute("select action, actor, after from change_log where record_table = 'export'").fetchone()
    assert row[0] == "export:docx" and row[1] == "Dr Test"
    assert row[2]["sha256"] == out["sha256"] and row[2]["claim_ids"] == out["claims"]


def test_long_sections_continue_on_a_new_slide():
    doc = {"title": "T", "audience": "A", "signed": "Dr X, 1 Jan 2026", "ai_note": "note",
           "references": [], "approved_texts": [f"Claim {i}." for i in range(8)], "other_texts": [],
           "sections": [{"heading": "Big", "items": [{"claim_id": i, "text": f"Claim {i}.", "refs": [],
                                                      "assertion_ids": [], "grades": []} for i in range(8)]}]}
    text = export.pptx_text(export.to_pptx(doc))
    assert "Big (cont.)" in text and "Claim 7." in text
