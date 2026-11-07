"""Drafts and the doctor's review: save, edit (re-verified), approve/reject, propose a spine
correction, sign off. Every change is logged with the reviewer's name."""
from __future__ import annotations

from decimal import Decimal

from psycopg.types.json import Jsonb

from .agents import verifier
from .llm import LLM


class ReviewError(ValueError):
    pass


def _actor(cur, actor: str):
    if not actor or not actor.strip():
        raise ReviewError("enter your name first; every review action is logged with it")
    cur.execute("select set_config('evidex.actor', %s, true)", (actor.strip(),))
    return actor.strip()


def save(conn, run_id: str, data: dict) -> str:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("insert into draft (id, request, artifact, audience, title, model_not_covered, unused_rows) "
                    "values (%s, %s, %s, %s, %s, %s, %s)",
                    (run_id, data["request"], data["artifact"], data["audience"], data["title"],
                     Jsonb(data.get("model_not_covered", [])), Jsonb(data.get("unused_rows", {}))))
        for si, s in enumerate(data["sections"]):
            for ci, c in enumerate(s["claims"]):
                verdict = {**c["verdict"], "regenerated": c.get("regenerated", False),
                           "first_attempt": c.get("first_attempt")}
                cur.execute("insert into claim (draft_id, section, section_pos, pos, original_text, text, "
                            "assertion_ids, verdict) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                            (run_id, s["heading"], si, ci, c["text"], c["text"], c["assertion_ids"],
                             Jsonb(verdict)))
    return run_id


def _rows(cur):
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def list_drafts(conn, limit: int = 30) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("""select d.id, d.title, d.request, d.audience, d.artifact, d.status, d.created_at,
                              d.signed_by, count(c.id) as claims,
                              count(c.id) filter (where c.status = 'pending') as pending
                       from draft d left join claim c on c.draft_id = d.id
                       group by d.id order by d.created_at desc limit %s""", (limit,))
        return _rows(cur)


def load(conn, draft_id: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("select * from draft where id = %s", (draft_id,))
        d = _rows(cur)
        if not d:
            return None
        cur.execute("select * from claim where draft_id = %s order by section_pos, pos", (draft_id,))
        claims = _rows(cur)
    draft = d[0]
    sections: list[dict] = []
    for c in claims:
        if not sections or sections[-1]["heading"] != c["section"]:
            sections.append({"heading": c["section"], "claims": []})
        sections[-1]["claims"].append(c)
    draft["sections"] = sections
    draft["claims"] = claims
    return draft


def get_claim(conn, claim_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute("select c.*, d.status as draft_status from claim c join draft d on d.id = c.draft_id "
                    "where c.id = %s", (claim_id,))
        rows = _rows(cur)
    if not rows:
        raise ReviewError(f"no claim {claim_id}")
    return rows[0]


def _unlocked(c: dict):
    if c["draft_status"] == "signed_off":
        raise ReviewError("this draft is signed off and locked")


def edit(conn, llm: LLM, claim_id: int, actor: str, text: str) -> dict:
    """Doctor edits a claim. The Verifier re-runs on the new text; the claim goes back to pending."""
    c = get_claim(conn, claim_id)
    _unlocked(c)
    text = text.strip()
    if not text:
        raise ReviewError("a claim cannot be empty; reject it instead")
    v = verifier.verify_texts(conn, llm, [{"text": text, "assertion_ids": c["assertion_ids"]}],
                              run_id=c["draft_id"], log_conn=conn, step=f"verifier:edit:{claim_id}")[0]
    v.update(regenerated=c["verdict"].get("regenerated", False), edited=True,
             first_attempt=c["verdict"].get("first_attempt"))
    with conn.transaction(), conn.cursor() as cur:
        _actor(cur, actor)
        cur.execute("update claim set text = %s, verdict = %s, status = 'pending', numbers_checked = false, "
                    "reviewed_by = null, reviewed_at = null where id = %s", (text, Jsonb(v), claim_id))
    return get_claim(conn, claim_id)


def decide(conn, claim_id: int, actor: str, status: str, numbers_checked: bool = False) -> dict:
    if status not in ("approved", "rejected", "pending"):
        raise ReviewError(f"unknown decision {status}")
    c = get_claim(conn, claim_id)
    _unlocked(c)
    if status == "approved":
        if not c["verdict"].get("ok"):
            raise ReviewError("cannot approve a flagged claim: edit it until the Verifier passes, "
                              "or reject it")
        if c["verdict"].get("number_checks") and not numbers_checked:
            raise ReviewError("tick 'numbers checked' before approving a claim that contains numbers")
    with conn.transaction(), conn.cursor() as cur:
        _actor(cur, actor)
        cur.execute("update claim set status = %s, numbers_checked = %s, reviewed_by = %s, "
                    "reviewed_at = now() where id = %s",
                    (status, numbers_checked, actor.strip() if status != "pending" else None, claim_id))
    return get_claim(conn, claim_id)


def propose_correction(conn, claim_id: int, actor: str, number_check: dict) -> str:
    """Turn a doctor's disputed number into a `proposed` spine row. The spine itself is unchanged
    until the proposal is accepted in the Encyclopedia."""
    c = get_claim(conn, claim_id)
    aid = number_check.get("assertion_id")
    if not aid or aid not in c["assertion_ids"]:
        raise ReviewError("this number is not tied to one of the claim's spine rows")
    with conn.transaction(), conn.cursor() as cur:
        who = _actor(cur, actor)
        cur.execute("select * from assertion where id = %s", (aid,))
        rows = _rows(cur)
        if not rows:
            raise ReviewError(f"spine row {aid} not found")
        a = rows[0]
        if a["operator"] == "range":
            raise ReviewError("this row is a range; edit the range in the Encyclopedia instead")
        cur.execute("select count(*) from assertion where id like %s", (aid + "-prop-%",))
        new_id = f"{aid}-prop-{cur.fetchone()[0] + 1}"
        cur.execute("insert into source_anchor (id, doc, edition, section, locator, page, url) "
                    "select %s, doc, edition, section, locator, page, url from source_anchor where id = %s",
                    ("anc:" + new_id, a["source_anchor_id"]))
        cur.execute(
            "insert into assertion (id, subject_id, predicate, object_id, operator, value, value_high, unit, "
            "population_id, condition_text, grade, status, source_anchor_id, added_by, notes) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'proposed',%s,%s,%s)",
            (new_id, a["subject_id"], a["predicate"], a["object_id"], a["operator"],
             Decimal(number_check["value"]), None, a["unit"], a["population_id"], a["condition_text"],
             a["grade"], "anc:" + new_id, who,
             f"Proposed during review of draft {c['draft_id']} (claim {claim_id}): spine has "
             f"{a['value']} {a['unit']}, reviewer proposes {number_check['value']} {a['unit']}. "
             "Accept or reject in the Encyclopedia."))
    return new_id


def signoff_state(draft: dict) -> dict:
    claims = draft["claims"]
    pending = [c for c in claims if c["status"] == "pending"]
    approved = [c for c in claims if c["status"] == "approved"]
    flagged = [c for c in approved if not c["verdict"].get("ok")]
    reasons = []
    if draft["status"] == "signed_off":
        reasons.append("already signed off")
    if pending:
        reasons.append(f"{len(pending)} claim(s) still to approve or reject")
    if not approved:
        reasons.append("no approved claims")
    if flagged:
        reasons.append(f"{len(flagged)} approved claim(s) have open flags")
    unchecked = sorted({a["assertion_id"] for c in approved for a in c["verdict"].get("anchors", [])
                        if not a.get("doctor_checked")})
    return {"can_sign": not reasons, "reasons": reasons, "approved": len(approved),
            "rejected": sum(c["status"] == "rejected" for c in claims), "pending": len(pending),
            "unchecked_rows": unchecked}


def sign_off(conn, draft_id: str, actor: str) -> dict:
    d = load(conn, draft_id)
    if d is None:
        raise ReviewError(f"no draft {draft_id}")
    st = signoff_state(d)
    if not st["can_sign"]:
        raise ReviewError("cannot sign off: " + "; ".join(st["reasons"]))
    with conn.transaction(), conn.cursor() as cur:
        who = _actor(cur, actor)
        cur.execute("update draft set status = 'signed_off', signed_by = %s, signed_at = now() "
                    "where id = %s and status = 'in_review'", (who, draft_id))
    return load(conn, draft_id)
