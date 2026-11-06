"""Export a signed-off draft to PPTX or DOCX.

No model runs here: the exported text is exactly the approved claim text, laid out by code.
A fidelity check runs before every export and blocks it on any mismatch.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import datetime

from psycopg.types.json import Jsonb

from . import drafts
from .agents.steps import numbers_in

NOT_PROVIDED = "Not provided"
MAX_BULLETS = 6


class ExportError(ValueError):
    pass


# ---------------- document model ----------------

def _model_name(conn, draft_id: str) -> str:
    row = conn.execute("select distinct model from agent_call where run_id = %s limit 1", (draft_id,)).fetchone()
    return row[0] if row else "a local language model"


def build(conn, draft_id: str) -> dict:
    """The exportable document: approved claims only, numbered references, AI-use note."""
    d = drafts.load(conn, draft_id)
    if d is None:
        raise ExportError(f"no draft {draft_id}")
    if d["status"] != "signed_off":
        raise ExportError("only signed-off drafts can be exported")

    refs: dict[str, dict] = {}
    sections = []
    for s in d["sections"]:
        items = []
        for c in s["claims"]:
            if c["status"] != "approved":
                continue
            nums = []
            for an in c["verdict"].get("anchors", []):
                if an["assertion_id"] not in refs:
                    refs[an["assertion_id"]] = {"n": len(refs) + 1, **an}
                nums.append(refs[an["assertion_id"]]["n"])
            items.append({"claim_id": c["id"], "text": c["text"], "refs": sorted(set(nums)),
                          "assertion_ids": c["assertion_ids"],
                          "grades": c["verdict"].get("grades", [])})
        sections.append({"heading": s["heading"], "items": items})

    signed = f"{d['signed_by']}, {d['signed_at'].astimezone():%d %b %Y}"
    model = _model_name(conn, draft_id)
    note = (f"Prepared with Evidex. Drafted by a local AI model ({model}) from a doctor-curated "
            "diabetes knowledge base anchored to the ADA Standards of Care in Diabetes 2026 "
            "(Sections 2-3). Every number was checked against that knowledge base by code, and "
            f"every statement was reviewed and approved by {signed}. General teaching material: "
            "not advice about any individual patient.")
    return {"draft_id": d["id"], "title": d["title"], "audience": d["audience"],
            "artifact": d["artifact"], "request": d["request"], "signed": signed,
            "sections": sections, "references": sorted(refs.values(), key=lambda r: r["n"]),
            "ai_note": note, "approved_texts": [c["text"] for c in d["claims"] if c["status"] == "approved"],
            "other_texts": [c["text"] for c in d["claims"] if c["status"] != "approved"]}


def ref_tag(refs: list[int]) -> str:
    return " [" + ", ".join(map(str, refs)) + "]" if refs else ""


def reference_text(r: dict) -> str:
    text = f"{r['statement']}"
    if r.get("condition_text"):
        text += f" ({r['condition_text']})"
    text += f". {r['anchor']}."
    if not r.get("doctor_checked"):
        text += " Not yet checked by the doctor against the 2026 text."
    return text


# ---------------- fidelity ----------------

def fidelity(doc: dict) -> list[str]:
    problems = []
    approved = set(doc["approved_texts"])
    body = [i["text"] for s in doc["sections"] for i in s["items"]]
    for t in body:
        if t not in approved:
            problems.append(f"text is not an approved claim: {t[:80]}")
    for t in doc["other_texts"]:
        if t in body and t not in approved:
            problems.append(f"rejected or pending claim included: {t[:80]}")
    if sorted(body) != sorted(doc["approved_texts"]):
        problems.append("the export does not contain exactly the approved claims")
    allowed = set().union(*(numbers_in(t) for t in doc["approved_texts"])) if approved else set()
    for t in body:
        extra = numbers_in(t) - allowed
        if extra:
            problems.append(f"number(s) not in approved claims: {', '.join(map(str, extra))}")
    if not body:
        problems.append("no approved claims to export")
    return problems


def check_rendered(doc: dict, text: str) -> list[str]:
    """After rendering, every approved claim must appear verbatim in the file's text."""
    flat = re.sub(r"\s+", " ", text)
    return [f"approved claim missing from the file: {t[:80]}" for t in doc["approved_texts"]
            if re.sub(r"\s+", " ", t) not in flat]


# ---------------- PPTX ----------------

def to_pptx(doc: dict) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.util import Emu, Inches, Pt

    INK, MUTED, ACCENT = RGBColor(0x1F, 0x29, 0x37), RGBColor(0x5B, 0x67, 0x75), RGBColor(0x0F, 0x76, 0x6E)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W = prs.slide_width

    def box(slide, x, y, w, h, text, size, color=INK, bold=False):
        tb = slide.shapes.add_textbox(x, y, w, h)
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = text
        p.font.size, p.font.bold, p.font.color.rgb = Pt(size), bold, color
        return tf

    def footer(slide):
        box(slide, Inches(0.6), Inches(6.95), W - Inches(1.2), Inches(0.4),
            f"Evidex · approved by {doc['signed']} · {doc['audience']}", 11, MUTED)

    def bar(slide):
        shp = slide.shapes.add_shape(1, 0, 0, W, Inches(0.12))
        shp.fill.solid()
        shp.fill.fore_color.rgb = ACCENT
        shp.line.fill.background()

    # title
    s = prs.slides.add_slide(blank)
    bar(s)
    box(s, Inches(0.8), Inches(2.4), W - Inches(1.6), Inches(1.4), doc["title"], 40, INK, True)
    box(s, Inches(0.8), Inches(3.9), W - Inches(1.6), Inches(0.6), f"Teaching slides for {doc['audience']}", 22, MUTED)
    box(s, Inches(0.8), Inches(4.6), W - Inches(1.6), Inches(0.6), f"Approved by {doc['signed']}", 18, ACCENT)

    def content_slide(heading, lines, notes):
        s = prs.slides.add_slide(blank)
        bar(s)
        box(s, Inches(0.6), Inches(0.4), W - Inches(1.2), Inches(0.9), heading, 30, INK, True)
        tf = box(s, Inches(0.8), Inches(1.5), W - Inches(1.6), Inches(5.3), "", 20)
        size = 20 if len(lines) <= 4 else 17
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = "•  " + line
            p.font.size, p.font.color.rgb = Pt(size), INK
            p.space_after = Pt(10)
        footer(s)
        s.notes_slide.notes_text_frame.text = notes
        return s

    for sec in doc["sections"]:
        items = sec["items"]
        if not items:
            content_slide(sec["heading"], [NOT_PROVIDED], "All claims in this section were rejected in review.")
            continue
        for start in range(0, len(items), MAX_BULLETS):
            chunk = items[start:start + MAX_BULLETS]
            heading = sec["heading"] + (" (cont.)" if start else "")
            notes = "\n".join(f"[{', '.join(map(str, i['refs']))}] spine {', '.join(i['assertion_ids'])}; "
                              f"grade {', '.join(sorted(set(i['grades'])))}" for i in chunk)
            content_slide(heading, [i["text"] + ref_tag(i["refs"]) for i in chunk], notes)

    refs = doc["references"]
    per = 7
    for start in range(0, len(refs), per):
        chunk = refs[start:start + per]
        s = prs.slides.add_slide(blank)
        bar(s)
        box(s, Inches(0.6), Inches(0.4), W - Inches(1.2), Inches(0.9),
            "References" + (" (cont.)" if start else ""), 30, INK, True)
        tf = box(s, Inches(0.8), Inches(1.4), W - Inches(1.6), Inches(5.4), "", 12)
        for i, r in enumerate(chunk):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = f"[{r['n']}] {reference_text(r)}"
            p.font.size, p.font.color.rgb = Pt(12), INK
            p.space_after = Pt(6)
        footer(s)

    s = prs.slides.add_slide(blank)
    bar(s)
    box(s, Inches(0.6), Inches(0.4), W - Inches(1.2), Inches(0.9), "How this deck was made", 30, INK, True)
    box(s, Inches(0.8), Inches(1.6), W - Inches(1.6), Inches(4.5), doc["ai_note"], 18, INK)
    footer(s)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def pptx_text(data: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    return "\n".join(sh.text_frame.text for sl in prs.slides for sh in sl.shapes if sh.has_text_frame)


# ---------------- DOCX ----------------

def to_docx(doc: dict) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor

    d = Document()
    d.styles["Normal"].font.size = Pt(11)
    d.add_heading(doc["title"], level=0)
    meta = d.add_paragraph(f"Teaching handout for {doc['audience']} · approved by {doc['signed']}")
    meta.runs[0].font.color.rgb = RGBColor(0x5B, 0x67, 0x75)
    for sec in doc["sections"]:
        d.add_heading(sec["heading"], level=1)
        if not sec["items"]:
            d.add_paragraph(NOT_PROVIDED)
        for i in sec["items"]:
            d.add_paragraph(i["text"] + ref_tag(i["refs"]), style="List Bullet")
    d.add_heading("References", level=1)
    for r in doc["references"]:
        d.add_paragraph(f"[{r['n']}] {reference_text(r)}")
    d.add_heading("How this handout was made", level=1)
    d.add_paragraph(doc["ai_note"])
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def docx_text(data: bytes) -> str:
    from docx import Document
    return "\n".join(p.text for p in Document(io.BytesIO(data)).paragraphs)


# ---------------- entry points ----------------

FORMATS = {
    "pptx": (to_pptx, pptx_text, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "docx": (to_docx, docx_text, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
}


def render(conn, draft_id: str, fmt: str) -> dict:
    """Build, check and render. Raises ExportError if any fidelity check fails."""
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt}")
    doc = build(conn, draft_id)
    problems = fidelity(doc)
    make, read, mime = FORMATS[fmt]
    data = make(doc) if not problems else b""
    if not problems:
        problems = check_rendered(doc, read(data))
    if problems:
        raise ExportError("export blocked: " + "; ".join(problems))
    slug = re.sub(r"[^a-z0-9]+", "-", doc["title"].lower()).strip("-")[:50] or "evidex"
    return {"filename": f"{slug}.{fmt}", "data": data, "mime": mime,
            "sha256": hashlib.sha256(data).hexdigest(),
            "claims": [i["claim_id"] for s in doc["sections"] for i in s["items"]]}


def log_export(conn, draft_id: str, fmt: str, actor: str, sha256: str, claims: list[int]):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("insert into change_log (record_table, record_id, action, actor, after) "
                    "values ('export', %s, %s, %s, %s)",
                    (draft_id, f"export:{fmt}", actor.strip() or "unknown",
                     Jsonb({"sha256": sha256, "claim_ids": claims, "at": datetime.now().isoformat()})))
