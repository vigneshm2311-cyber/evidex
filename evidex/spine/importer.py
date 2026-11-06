"""Load spine CSVs, validate every row, and write them to Postgres.

Two files, edited by the doctor:
  concepts.csv    id,type,name,synonyms,notes            (synonyms separated by ';')
  assertions.csv  one row per guideline statement, plus its source anchor
                  (section, locator, page). The importer creates one
                  source_anchor per assertion with id 'anc:<assertion id>'.

All rows are validated first. Nothing is written if any row has an error.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .vocab import CONCEPT_TYPES, ID_PREFIX_TYPE, OPERATORS, PREDICATES, STATUSES

ANCHOR_DOC = "ADA Standards of Care in Diabetes"
ANCHOR_EDITION = "2026"

CONCEPT_COLS = ["id", "type", "name", "synonyms", "notes"]
ASSERTION_COLS = [
    "id", "subject_id", "predicate", "object_id", "operator", "value", "value_high",
    "unit", "population_id", "condition_text", "grade", "status", "section", "locator",
    "page", "added_by", "reviewed_by", "doctor_checked", "notes",
]
CONCEPT_ID_RE = re.compile(r"^[a-z]+:[a-z0-9_]+$")


@dataclass
class Problem:
    file: str
    line: int
    column: str
    message: str

    def __str__(self) -> str:
        return f"{self.file} line {self.line}, column '{self.column}': {self.message}"


@dataclass
class Spine:
    concepts: list[dict] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)


def _read(path: Path, expected: list[str], problems: list[Problem]) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in expected if c not in header]
        extra = [c for c in header if c not in expected]
        if missing:
            problems.append(Problem(path.name, 1, ",".join(missing), "missing column(s)"))
        if extra:
            problems.append(Problem(path.name, 1, ",".join(extra), "unknown column(s)"))
        rows = []
        for i, row in enumerate(reader, start=2):
            clean = {k: (v or "").strip() for k, v in row.items() if k is not None}
            if not any(clean.values()):
                continue  # blank line
            clean["_line"] = i
            rows.append(clean)
        return rows


def _num(s: str) -> Decimal | None:
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def validate(spine: Spine, known_concepts: dict[str, str] | None = None) -> list[Problem]:
    """Check every row. known_concepts maps id -> type for concepts already in the DB."""
    problems: list[Problem] = []
    types: dict[str, str] = dict(known_concepts or {})

    seen: set[str] = set()
    for r in spine.concepts:
        P = lambda col, msg: problems.append(Problem("concepts.csv", r["_line"], col, msg))
        cid, ctype = r.get("id", ""), r.get("type", "")
        if not CONCEPT_ID_RE.match(cid):
            P("id", f"'{cid}' must look like prefix:name (lowercase, digits, underscores)")
        elif cid in seen:
            P("id", f"duplicate concept id '{cid}'")
        seen.add(cid)
        if ctype not in CONCEPT_TYPES:
            P("type", f"'{ctype}' is not a concept type (allowed: {', '.join(sorted(CONCEPT_TYPES))})")
        prefix = cid.split(":", 1)[0]
        if prefix not in ID_PREFIX_TYPE:
            P("id", f"unknown prefix '{prefix}' (allowed: {', '.join(sorted(ID_PREFIX_TYPE))})")
        elif ctype in CONCEPT_TYPES and ID_PREFIX_TYPE[prefix] != ctype:
            P("type", f"prefix '{prefix}:' means {ID_PREFIX_TYPE[prefix]}, but type is {ctype}")
        if not r.get("name"):
            P("name", "name is required")
        types[cid] = ctype

    seen = set()
    for r in spine.assertions:
        P = lambda col, msg: problems.append(Problem("assertions.csv", r["_line"], col, msg))
        aid = r.get("id", "")
        if not aid:
            P("id", "id is required")
        elif aid in seen:
            P("id", f"duplicate assertion id '{aid}'")
        seen.add(aid)

        pred = r.get("predicate", "")
        if pred not in PREDICATES:
            P("predicate", f"'{pred}' is not an allowed predicate (allowed: {', '.join(PREDICATES)})")

        for col, required in (("subject_id", True), ("object_id", False), ("population_id", False)):
            ref = r.get(col, "")
            if not ref:
                if required:
                    P(col, "required")
                continue
            if ref not in types:
                P(col, f"concept '{ref}' does not exist in concepts.csv or the database")
            elif col == "population_id" and types[ref] != "Population":
                P(col, f"'{ref}' is a {types[ref]}, not a Population")

        op, val, high, unit = (r.get(k, "") for k in ("operator", "value", "value_high", "unit"))
        if op and op not in OPERATORS:
            P("operator", f"'{op}' is not allowed (use one of {' '.join(sorted(OPERATORS))})")
        if val and _num(val) is None:
            P("value", f"'{val}' is not a number")
        if high and _num(high) is None:
            P("value_high", f"'{high}' is not a number")
        if val or op or high:
            if not (val and op):
                P("operator", "a value needs both an operator and a value")
            if val and not unit:
                P("unit", "a value needs a unit")
        if op == "range":
            if not high:
                P("value_high", "operator 'range' needs value (low) and value_high (high)")
            elif _num(val) is not None and _num(high) is not None and _num(val) >= _num(high):
                P("value_high", f"range low ({val}) must be below high ({high})")
        elif high:
            P("value_high", "value_high is only used with operator 'range'")
        if pred in PREDICATES and PREDICATES[pred] and not val:
            P("value", f"predicate '{pred}' requires a value")

        if r.get("status", "") not in STATUSES:
            P("status", f"'{r.get('status')}' is not a status (allowed: {', '.join(sorted(STATUSES))})")
        if not r.get("section"):
            P("section", "the ADA section number is required")
        if not r.get("added_by"):
            P("added_by", "required")
        if r.get("doctor_checked", "").lower() not in ("", "true", "false", "yes", "no"):
            P("doctor_checked", "use true or false")

    return problems


def load(directory: Path) -> tuple[Spine, list[Problem]]:
    problems: list[Problem] = []
    spine = Spine(
        concepts=_read(directory / "concepts.csv", CONCEPT_COLS, problems),
        assertions=_read(directory / "assertions.csv", ASSERTION_COLS, problems),
    )
    return spine, problems


def _bool(s: str) -> bool:
    return s.lower() in ("true", "yes")


def _none(s: str):
    return s if s != "" else None


def concept_params(r: dict) -> tuple:
    syn = [s.strip() for s in r.get("synonyms", "").split(";") if s.strip()]
    return (r["id"], r["type"], r["name"], syn, _none(r.get("notes", "")))


def anchor_params(r: dict) -> tuple:
    return ("anc:" + r["id"], ANCHOR_DOC, ANCHOR_EDITION, r["section"],
            _none(r["locator"]), _none(r["page"]), None)


def assertion_params(r: dict) -> tuple:
    return (
        r["id"], r["subject_id"], r["predicate"], _none(r["object_id"]), _none(r["operator"]),
        _none(r["value"]), _none(r["value_high"]), _none(r["unit"]), _none(r["population_id"]),
        _none(r["condition_text"]), _none(r["grade"]), r["status"], "anc:" + r["id"],
        r["added_by"], _none(r["reviewed_by"]), _bool(r["doctor_checked"]), _none(r["notes"]),
    )


UPSERT_CONCEPT = """
insert into concept (id, type, name, synonyms, notes) values (%s, %s, %s, %s, %s)
on conflict (id) do update set type = excluded.type, name = excluded.name,
    synonyms = excluded.synonyms, notes = excluded.notes"""

UPSERT_ANCHOR = """
insert into source_anchor (id, doc, edition, section, locator, page, url)
values (%s, %s, %s, %s, %s, %s, %s)
on conflict (id) do update set doc = excluded.doc, edition = excluded.edition,
    section = excluded.section, locator = excluded.locator, page = excluded.page"""

UPSERT_ASSERTION = """
insert into assertion (id, subject_id, predicate, object_id, operator, value, value_high,
    unit, population_id, condition_text, grade, status, source_anchor_id, added_by,
    reviewed_by, doctor_checked, notes)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
on conflict (id) do update set subject_id = excluded.subject_id, predicate = excluded.predicate,
    object_id = excluded.object_id, operator = excluded.operator, value = excluded.value,
    value_high = excluded.value_high, unit = excluded.unit,
    population_id = excluded.population_id, condition_text = excluded.condition_text,
    grade = excluded.grade, status = excluded.status, added_by = excluded.added_by,
    reviewed_by = excluded.reviewed_by, doctor_checked = excluded.doctor_checked,
    notes = excluded.notes"""


def write(conn, spine: Spine, actor: str) -> dict[str, int]:
    """Upsert everything in one transaction. Call validate() first."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("select set_config('evidex.actor', %s, true)", (actor,))
            for r in spine.concepts:
                cur.execute(UPSERT_CONCEPT, concept_params(r))
            for r in spine.assertions:
                cur.execute(UPSERT_ANCHOR, anchor_params(r))
                cur.execute(UPSERT_ASSERTION, assertion_params(r))
    return {"concepts": len(spine.concepts), "assertions": len(spine.assertions)}


def _lit(v) -> str:
    """SQL literal for the --emit-sql path (used to load Supabase through its SQL tool)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, list):
        return "array[" + ",".join(_lit(x) for x in v) + "]::text[]" if v else "'{}'::text[]"
    return "'" + str(v).replace("'", "''") + "'"


def to_sql(spine: Spine, actor: str) -> str:
    """One multi-row upsert per table, for loading through an SQL console."""
    def multi(template: str, rows: list[tuple]) -> str:
        head, tail = template.split("values", 1)
        conflict = tail[tail.index(")") + 1:]
        vals = ",\n".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in rows)
        return head.strip() + " values\n" + vals + conflict.rstrip() + ";"

    return "\n".join([
        "begin;",
        f"select set_config('evidex.actor', {_lit(actor)}, true);",
        multi(UPSERT_CONCEPT, [concept_params(r) for r in spine.concepts]),
        multi(UPSERT_ANCHOR, [anchor_params(r) for r in spine.assertions]),
        multi(UPSERT_ASSERTION, [assertion_params(r) for r in spine.assertions]),
        "commit;",
    ])


def known_concepts(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("select id, type from concept")
        return dict(cur.fetchall())
