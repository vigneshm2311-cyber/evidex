"""Read API and doctor-edit tests against a small fixed spine (not the private seed)."""
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidex.spine import api, importer  # noqa: E402

CONCEPTS = """id,type,name,synonyms,notes
cond:diabetes,Condition,Diabetes mellitus,DM,
cond:prediabetes,Condition,Prediabetes,,
cond:t2d,Condition,Type 2 diabetes,T2D,
cond:sickle_cell_disease,Condition,Sickle cell disease,,
test:a1c,Test,Glycated haemoglobin (A1C),HbA1c,
test:fpg,Test,Fasting plasma glucose,FPG,
test:plasma_glucose,Test,Plasma glucose criteria,,
pop:general,Population,General,,
pop:adults_all,Population,All adults,,
"""
H = ",".join(importer.ASSERTION_COLS)
ROWS = [
    "dx-a1c,cond:diabetes,diagnosed_by,test:a1c,>=,6.5,,%,pop:general,,,accepted,2,,,t,,false,",
    "dx-fpg,cond:diabetes,diagnosed_by,test:fpg,>=,126,,mg/dL,pop:general,,,accepted,2,,,t,,false,",
    "pre-a1c,cond:prediabetes,diagnosed_by,test:a1c,range,5.7,6.4,%,pop:general,,,accepted,2,,,t,,false,",
    "a1c-sickle,test:a1c,unreliable_when,cond:sickle_cell_disease,,,,,pop:general,,,accepted,2,,,t,,false,",
    "use-glucose,test:a1c,use_instead,test:plasma_glucose,,,,,pop:general,,,accepted,2,,,t,,false,",
    "start-35,cond:t2d,screening_starts_at,test:a1c,>=,35,,years,pop:adults_all,,,accepted,2,,,t,,false,",
    "draft-row,cond:diabetes,diagnosed_by,test:fpg,>=,999,,mg/dL,pop:general,,,draft,2,,,t,,false,",
]


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
    spine, problems = importer.load(tmp_path)
    assert not problems + importer.validate(spine)
    importer.write(c, spine, "seed")
    yield c
    c.close()


def ids(rows):
    return sorted(r["id"] for r in rows)


def test_get_assertions_filters(conn):
    assert ids(api.get_assertions(conn, "cond:diabetes")) == ["dx-a1c", "dx-fpg"]  # draft excluded
    assert ids(api.get_assertions(conn, "cond:diabetes", status=None)) == ["draft-row", "dx-a1c", "dx-fpg"]
    assert ids(api.get_assertions(conn, "test:a1c", role="subject")) == ["a1c-sickle", "use-glucose"]
    assert ids(api.get_assertions(conn, "test:a1c", predicate="diagnosed_by")) == ["dx-a1c", "pre-a1c"]
    assert ids(api.get_assertions(conn, "cond:t2d", population="pop:adults_all")) == ["start-35"]


def test_statement_and_anchor_text(conn):
    a = api.get_assertion(conn, "pre-a1c")
    assert api.statement(a) == "Prediabetes is diagnosed by Glycated haemoglobin (A1C) 5.7–6.4 %"
    assert api.format_value(api.get_assertion(conn, "dx-a1c")) == "≥ 6.5 %"
    assert "Rec/Table TODO(doctor)" in api.anchor_text(a) and "p. TODO(doctor)" in api.anchor_text(a)


def test_resolve_by_synonym(conn):
    assert [c["id"] for c in api.resolve(conn, "what is the HbA1c cut-off")] == ["test:a1c"]
    assert api.resolve(conn, "DM") == []  # too short to match safely


def test_outline_groups_in_teaching_order(conn):
    o = api.outline_for(conn, "Using HbA1c to diagnose diabetes")
    headings = [s["heading"] for s in o["sections"]]
    assert headings == ["Diagnostic criteria", "When A1C is unreliable", "Screening"]
    diag = ids(o["sections"][0]["assertions"])
    assert diag == ["dx-a1c", "dx-fpg", "pre-a1c"]


def test_outline_not_in_spine(conn):
    o = api.outline_for(conn, "insulin pump settings")
    assert o["concepts"] == [] and o["sections"] == []


@pytest.mark.parametrize("args,status", [
    (("cond:diabetes", "diagnosed_by", 6.5, "%"), "match"),
    (("cond:diabetes", "diagnosed_by", "126", "mg/dL"), "match"),
    (("cond:diabetes", "diagnosed_by", 6.0, "%"), "mismatch"),
    (("cond:diabetes", "diagnosed_by", 126, "%"), "mismatch"),          # right number, wrong unit
    (("cond:diabetes", "diagnosed_by", 999, "mg/dL"), "mismatch"),      # draft row not trusted
    (("cond:prediabetes", "diagnosed_by", 5.7, "%"), "match"),          # one end of a range
    (("cond:t2d", "repeat_interval", 3, "years"), "not_in_spine"),
])
def test_check_value(conn, args, status):
    assert api.check_value(conn, *args)["status"] == status


def test_check_value_with_range_and_object(conn):
    r = api.check_value(conn, "cond:prediabetes", "diagnosed_by", 5.7, "%", value_high=6.5)
    assert r["status"] == "mismatch"
    assert r["expected"][0]["value"] == "5.7–6.4 %"
    r = api.check_value(conn, "cond:diabetes", "diagnosed_by", 6.5, "%", object_id="test:fpg")
    assert r["status"] == "mismatch"


def test_doctor_edit_is_logged_with_actor(conn):
    a = api.update_assertion(conn, "dx-a1c", "Dr Test",
                             {"grade": "B", "locator": "Rec 2.x", "page": "S30",
                              "doctor_checked": True})
    assert (a["grade"], a["locator"], a["page"], a["doctor_checked"], a["reviewed_by"]) == (
        "B", "Rec 2.x", "S30", True, "Dr Test")
    hist = api.change_history(conn, "dx-a1c")
    assert {(h["action"], h["actor"]) for h in hist[:2]} == {("update", "Dr Test")}
    assert len(hist) == 3  # insert + assertion update + anchor update


def test_bad_edit_saves_nothing(conn):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        api.update_assertion(conn, "pre-a1c", "Dr Test",
                             {"value": Decimal("6.4"), "value_high": Decimal("5.7"), "page": "S1"})
    a = api.get_assertion(conn, "pre-a1c")
    assert (a["value"], a["page"]) == (Decimal("5.7"), None)
    with pytest.raises(ValueError, match="reviewer name"):
        api.update_assertion(conn, "pre-a1c", " ", {"grade": "A"})
    with pytest.raises(ValueError, match="not editable"):
        api.update_assertion(conn, "pre-a1c", "Dr Test", {"predicate": "treats"})


def test_export_round_trips_through_importer(conn, tmp_path):
    api.update_assertion(conn, "dx-a1c", "Dr Test", {"locator": "Table 2.1", "doctor_checked": True})
    out = tmp_path / "out"
    out.mkdir()
    for name, text in api.export_csv(conn).items():
        (out / name).write_text(text)
    spine, problems = importer.load(out)
    assert problems + importer.validate(spine) == []
    row = next(r for r in spine.assertions if r["id"] == "dx-a1c")
    assert (row["locator"], row["doctor_checked"], row["value"]) == ("Table 2.1", "true", "6.5")
