"""Spine importer tests. DB tests use TEST_DATABASE_URL (default: local evidex_test)."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evidex.spine import importer  # noqa: E402

CONCEPTS = """id,type,name,synonyms,notes
cond:diabetes,Condition,Diabetes mellitus,DM,
test:a1c,Test,A1C,HbA1c,
pop:general,Population,General,,
"""
HEADER = ",".join(importer.ASSERTION_COLS)
GOOD = "a1,cond:diabetes,diagnosed_by,test:a1c,>=,6.5,,%,pop:general,,,accepted,2,,,claude,,false,"


def make(tmp_path, assertion_rows, concepts=CONCEPTS):
    (tmp_path / "concepts.csv").write_text(concepts)
    (tmp_path / "assertions.csv").write_text(HEADER + "\n" + "\n".join(assertion_rows) + "\n")
    spine, problems = importer.load(tmp_path)
    return spine, problems + importer.validate(spine)


def messages(problems):
    return [str(p) for p in problems]


def test_good_row_passes(tmp_path):
    spine, problems = make(tmp_path, [GOOD])
    assert problems == []
    assert len(spine.assertions) == 1


@pytest.mark.parametrize("row,expected", [
    (GOOD.replace("diagnosed_by", "treats"), "'treats' is not an allowed predicate"),
    (GOOD.replace("test:a1c", "test:missing"), "concept 'test:missing' does not exist"),
    (GOOD.replace("pop:general", "test:a1c"), "is a Test, not a Population"),
    (GOOD.replace(",6.5,", ",six,"), "'six' is not a number"),
    (GOOD.replace(",%,", ",,"), "a value needs a unit"),
    (GOOD.replace(">=,6.5,,", "range,6.4,5.7,"), "range low (6.4) must be below high (5.7)"),
    (GOOD.replace(">=,6.5,,", "range,6.5,,"), "operator 'range' needs value (low) and value_high"),
    (GOOD.replace("accepted", "approved"), "'approved' is not a status"),
    (GOOD.replace(",2,", ",,", 1), "the ADA section number is required"),
    ("a2,cond:diabetes,repeat_interval,test:a1c,,,,,pop:general,,,accepted,2,,,claude,,false,",
     "predicate 'repeat_interval' requires a value"),
])
def test_bad_rows_are_reported_with_line_and_column(tmp_path, row, expected):
    _, problems = make(tmp_path, [row])
    msgs = messages(problems)
    assert any(expected in m for m in msgs), msgs
    assert all("assertions.csv line 2" in m for m in msgs), msgs


def test_duplicate_ids(tmp_path):
    _, problems = make(tmp_path, [GOOD, GOOD])
    assert any("duplicate assertion id 'a1'" in m for m in messages(problems))


def test_concept_prefix_must_match_type(tmp_path):
    bad = CONCEPTS + "test:wrong,Condition,Wrong,,\n"
    _, problems = make(tmp_path, [GOOD], concepts=bad)
    assert any("prefix 'test:' means Test, but type is Condition" in m for m in messages(problems))


SEED = ROOT / "data" / "spine"
needs_seed = pytest.mark.skipif(not (SEED / "assertions.csv").exists(),
                                reason="spine seed CSVs are not in the repo (kept private)")


@needs_seed
def test_seed_data_is_valid():
    spine, problems = importer.load(ROOT / "data" / "spine")
    problems += importer.validate(spine)
    assert problems == [], "\n".join(map(str, problems))


# ---- database ----

@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    url = os.environ.get("TEST_DATABASE_URL", "postgresql:///evidex_test")
    try:
        c = psycopg.connect(url, autocommit=True)
    except psycopg.OperationalError:
        pytest.skip("test database not available")
    c.execute("drop schema public cascade; create schema public;")
    for f in sorted((ROOT / "migrations").glob("*.sql")):
        c.execute(f.read_text())
    yield c
    c.close()


def test_write_and_change_log(conn, tmp_path):
    spine, problems = make(tmp_path, [GOOD])
    assert problems == []
    importer.write(conn, spine, "dr-test")
    row = conn.execute("select value, unit, status, source_anchor_id from assertion").fetchone()
    assert (float(row[0]), row[1], row[2], row[3]) == (6.5, "%", "accepted", "anc:a1")
    log = conn.execute("select action, actor from change_log").fetchall()
    assert log == [("insert:accepted", "dr-test")]

    # re-import unchanged: no new log row
    importer.write(conn, spine, "dr-test")
    assert conn.execute("select count(*) from change_log").fetchone()[0] == 1

    # status change is logged as a transition
    conn.execute("select set_config('evidex.actor', 'dr-vignesh', false)")
    conn.execute("update assertion set status = 'superseded' where id = 'a1'")
    assert conn.execute(
        "select action, actor from change_log order by id desc limit 1").fetchone() == (
        "accepted->superseded", "dr-vignesh")


def test_db_rejects_bad_predicate_even_without_importer(conn, tmp_path):
    import psycopg
    spine, _ = make(tmp_path, [GOOD])
    importer.write(conn, spine, "t")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("update assertion set predicate = 'treats' where id = 'a1'")
    with pytest.raises(psycopg.errors.RaiseException, match="not a Population"):
        conn.execute("update assertion set population_id = 'test:a1c' where id = 'a1'")


@needs_seed
def test_full_seed_loads(conn):
    spine, problems = importer.load(ROOT / "data" / "spine")
    assert problems == []
    importer.write(conn, spine, "t")
    n = conn.execute("select count(*) from assertion").fetchone()[0]
    assert n == len(spine.assertions)


@needs_seed
def test_emit_sql_matches_direct_write(conn, tmp_path):
    src = ROOT / "data" / "spine"
    spine, _ = importer.load(src)
    conn.execute(importer.to_sql(spine, "sql-path"))
    n = conn.execute("select count(*) from assertion").fetchone()[0]
    assert n == len(spine.assertions)
    assert conn.execute("select distinct actor from change_log").fetchall() == [("sql-path",)]
