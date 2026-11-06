"""Read (and doctor-edit) API over the spine. Agents and the UI use only this module.

The spine is an encyclopedia: these functions report what the guideline says and
where. Nothing here reasons about a particular patient.
"""
from __future__ import annotations

import csv
import io
import re
from decimal import Decimal

from .importer import ASSERTION_COLS, CONCEPT_COLS

ASSERTION_SQL = """
select a.id, a.subject_id, s.name as subject_name, s.type as subject_type,
       a.predicate, a.object_id, o.name as object_name, o.type as object_type,
       a.operator, a.value, a.value_high, a.unit,
       a.population_id, p.name as population_name,
       a.condition_text, a.grade, a.status, a.added_by, a.reviewed_by,
       a.doctor_checked, a.notes, a.updated_at,
       an.id as anchor_id, an.doc, an.edition, an.section, an.locator, an.page, an.url
from assertion a
join concept s on s.id = a.subject_id
left join concept o on o.id = a.object_id
left join concept p on p.id = a.population_id
join source_anchor an on an.id = a.source_anchor_id
"""

PREDICATE_LABEL = {
    "classified_as": "is classified as a type of",
    "subtype_of": "is a subtype of",
    "diagnosed_by": "is diagnosed by",
    "has_threshold": "has threshold",
    "requires_confirmation": "requires confirmation by",
    "unreliable_when": "is unreliable in",
    "use_instead": "→ use instead:",
    "screen_with": "is screened for with",
    "screening_starts_at": "screening starts at age, using",
    "repeat_interval": "is re-tested at an interval, using",
    "risk_factor_for": "is a risk factor for",
    "defined_by": "is defined by",
    "prevented_or_delayed_by": "may be prevented or delayed by",
    "has_target": "has the target",
    "requires_monitoring_of": "requires periodic monitoring of",
}

# Teaching order for outlines: (section heading, predicates)
OUTLINE_ORDER = [
    ("Classification", ["classified_as", "subtype_of"]),
    ("Diagnostic criteria", ["diagnosed_by", "requires_confirmation", "has_threshold"]),
    ("When A1C is unreliable", ["unreliable_when", "use_instead"]),
    ("Screening", ["screen_with", "screening_starts_at", "repeat_interval", "risk_factor_for"]),
    ("Definitions", ["defined_by"]),
    ("Prevention or delay", ["prevented_or_delayed_by", "has_target", "requires_monitoring_of"]),
]

OPS = {">=": "≥", ">": ">", "<=": "≤", "<": "<", "=": "="}


def _rows(cur) -> list[dict]:
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _fmt_num(v: Decimal | None) -> str:
    if v is None:
        return ""
    s = format(Decimal(v).normalize(), "f")
    return s


def format_value(a: dict) -> str:
    if a.get("value") is None:
        return ""
    unit = a.get("unit") or ""
    if a["operator"] == "range":
        return f"{_fmt_num(a['value'])}–{_fmt_num(a['value_high'])} {unit}".strip()
    return f"{OPS.get(a['operator'], a['operator'])} {_fmt_num(a['value'])} {unit}".strip()


def statement(a: dict) -> str:
    """One readable line, e.g. 'Diabetes mellitus is diagnosed by A1C ≥ 6.5 %'."""
    parts = [a["subject_name"], PREDICATE_LABEL.get(a["predicate"], a["predicate"])]
    if a.get("object_name"):
        parts.append(a["object_name"])
    v = format_value(a)
    if v:
        parts.append(v)
    s = " ".join(parts)
    if a.get("population_name") and a.get("population_id") != "pop:general":
        s += f" (population: {a['population_name']})"
    return s


def anchor_text(a: dict) -> str:
    return (f"{a['doc']} {a['edition']} · Section {a['section']} · "
            f"{a['locator'] or 'Rec/Table TODO(doctor)'} · p. {a['page'] or 'TODO(doctor)'}")


# ---------- reads ----------

def all_concepts(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("select id, type, name, synonyms, notes from concept order by type, name")
        return _rows(cur)


def all_assertions(conn, status: str | None = None) -> list[dict]:
    with conn.cursor() as cur:
        if status:
            cur.execute(ASSERTION_SQL + " where a.status = %s order by a.id", (status,))
        else:
            cur.execute(ASSERTION_SQL + " order by a.id")
        return _rows(cur)


def get_assertion(conn, assertion_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(ASSERTION_SQL + " where a.id = %s", (assertion_id,))
        rows = _rows(cur)
        return rows[0] if rows else None


def get_assertions(conn, concept: str, predicate: str | None = None,
                   population: str | None = None, status: str | None = "accepted",
                   role: str = "any") -> list[dict]:
    """Assertions mentioning a concept. role: 'subject', 'object', 'population' or 'any'."""
    roles = {"subject": ["a.subject_id"], "object": ["a.object_id"],
             "population": ["a.population_id"],
             "any": ["a.subject_id", "a.object_id", "a.population_id"]}[role]
    where = ["(" + " or ".join(f"{r} = %(c)s" for r in roles) + ")"]
    params = {"c": concept}
    if predicate:
        where.append("a.predicate = %(p)s")
        params["p"] = predicate
    if population:
        where.append("a.population_id = %(pop)s")
        params["pop"] = population
    if status:
        where.append("a.status = %(s)s")
        params["s"] = status
    with conn.cursor() as cur:
        cur.execute(ASSERTION_SQL + " where " + " and ".join(where) + " order by a.id", params)
        return _rows(cur)


def _terms(c: dict) -> list[str]:
    return [t for t in [c["name"], *c["synonyms"], c["id"].split(":", 1)[1].replace("_", " ")] if t]


def _mentions(text: str, term: str) -> bool:
    term = term.lower().strip()
    if len(term) < 3:
        return False  # 'DM', 'CF' etc. match too much
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


def resolve(conn, text: str, concepts: list[dict] | None = None) -> list[dict]:
    """Concepts named in free text (by id, name or synonym), best matches first."""
    t = text.lower()
    found = []
    for c in concepts if concepts is not None else all_concepts(conn):
        if c["id"] == t:
            return [c]
        hits = [term for term in _terms(c) if _mentions(t, term)]
        if hits:
            found.append((max(len(h) for h in hits), c))
    found.sort(key=lambda x: -x[0])
    return [c for _, c in found]


def outline_for(conn, topic: str) -> dict:
    """Spine-backed outline for a topic: accepted assertions grouped in teaching order.

    Returns {"topic", "concepts": [...], "sections": [{"heading", "assertions": [...]}]}.
    Empty sections are dropped; no concepts means the spine cannot cover the topic.
    """
    concepts = resolve(conn, topic)
    ids = {c["id"] for c in concepts}
    if not ids:
        return {"topic": topic, "concepts": [], "sections": []}
    rows = [a for a in all_assertions(conn, status="accepted")
            if a["subject_id"] in ids or a["object_id"] in ids or a["population_id"] in ids]
    sections = []
    for heading, preds in OUTLINE_ORDER:
        items = [a for a in rows if a["predicate"] in preds]
        if items:
            sections.append({"heading": heading, "assertions": items})
    return {"topic": topic, "concepts": concepts, "sections": sections}


def _norm_unit(u: str | None) -> str:
    return re.sub(r"\s+", "", (u or "").lower())


def check_value(conn, concept: str, predicate: str, value, unit: str,
                object_id: str | None = None, population: str | None = None,
                value_high=None) -> dict:
    """Compare a number in a draft against accepted spine values.

    status: 'match' | 'mismatch' | 'not_in_spine'. 'expected' lists the spine values
    that could apply, with their assertion ids, so a reviewer can see the right one.
    """
    rows = [a for a in get_assertions(conn, concept, predicate, population, role="subject")
            if a["value"] is not None and (object_id is None or a["object_id"] == object_id)]
    if not rows:
        return {"status": "not_in_spine", "expected": [], "assertion_ids": []}
    v = Decimal(str(value))
    vh = Decimal(str(value_high)) if value_high is not None else None
    expected = [{"assertion_id": a["id"], "value": format_value(a), "statement": statement(a)}
                for a in rows]
    for a in rows:
        if _norm_unit(a["unit"]) != _norm_unit(unit):
            continue
        if a["operator"] == "range":
            same = v == a["value"] and (vh is None or vh == a["value_high"])
            if vh is None and not same:
                same = v == a["value_high"]  # a single number quoting either end
        else:
            same = v == a["value"] and vh is None
        if same:
            return {"status": "match", "expected": expected, "assertion_ids": [a["id"]]}
    return {"status": "mismatch", "expected": expected, "assertion_ids": [a["id"] for a in rows]}


# ---------- doctor edits ----------

EDITABLE = ("operator", "value", "value_high", "unit", "condition_text", "grade", "status",
            "doctor_checked", "notes")
ANCHOR_EDITABLE = ("locator", "page")


def update_assertion(conn, assertion_id: str, actor: str, changes: dict) -> dict:
    """Doctor edit of one assertion and its anchor, in one transaction, logged with actor.

    The database enforces the value rules (range, unit, predicate requirements);
    a violation raises and nothing is saved.
    """
    if not actor or not actor.strip():
        raise ValueError("a reviewer name is required to edit the spine")
    bad = set(changes) - set(EDITABLE) - set(ANCHOR_EDITABLE)
    if bad:
        raise ValueError(f"not editable: {', '.join(sorted(bad))}")
    a_changes = {k: v for k, v in changes.items() if k in EDITABLE}
    n_changes = {k: v for k, v in changes.items() if k in ANCHOR_EDITABLE}
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute("select set_config('evidex.actor', %s, true)", (actor.strip(),))
            if a_changes:
                a_changes["reviewed_by"] = actor.strip()
                sets = ", ".join(f"{k} = %({k})s" for k in a_changes)
                cur.execute(f"update assertion set {sets} where id = %(id)s",
                            {**a_changes, "id": assertion_id})
                if cur.rowcount != 1:
                    raise ValueError(f"no assertion {assertion_id}")
            if n_changes:
                sets = ", ".join(f"{k} = %({k})s" for k in n_changes)
                cur.execute(f"update source_anchor set {sets} where id = "
                            f"(select source_anchor_id from assertion where id = %(id)s)",
                            {**n_changes, "id": assertion_id})
    return get_assertion(conn, assertion_id)


def change_history(conn, record_id: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("select action, actor, at, before, after from change_log "
                    "where record_id in (%s, %s) order by at desc, id desc",
                    (record_id, "anc:" + record_id))
        return _rows(cur)


# ---------- export back to the CSV format ----------

def export_csv(conn) -> dict[str, str]:
    """The current database as concepts.csv and assertions.csv text (importer format)."""
    out = {}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CONCEPT_COLS)
    for c in all_concepts(conn):
        w.writerow([c["id"], c["type"], c["name"], "; ".join(c["synonyms"]), c["notes"] or ""])
    out["concepts.csv"] = buf.getvalue()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(ASSERTION_COLS)
    for a in all_assertions(conn):
        rec = {**a, "value": _fmt_num(a["value"]), "value_high": _fmt_num(a["value_high"]),
               "doctor_checked": "true" if a["doctor_checked"] else "false"}
        w.writerow(["" if rec.get(c) is None else rec.get(c) for c in ASSERTION_COLS])
    out["assertions.csv"] = buf.getvalue()
    return out
