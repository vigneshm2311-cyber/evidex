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
from .agents.steps import PATIENT_NOTE, PATIENT_PERSONA, numbers_in

NOT_PROVIDED = "Not provided"
MAX_BULLETS = 6


class ExportError(ValueError):
    pass


# ---------------- document model ----------------

def _model_name(conn, draft_id: str) -> str:
    row = conn.execute("select distinct model from agent_call where run_id = %s limit 1", (draft_id,)).fetchone()
    return row[0] if row else "a language model"


def ai_note(model: str, signed: str, has_literature: bool, audience: str) -> str:
    sources = ("a doctor-curated diabetes knowledge base anchored to the ADA Standards of Care in Diabetes "
               "2026 (Sections 2-3)")
    if has_literature:
        sources += ", with context from the published papers and files listed in the references"
    note = (f"Prepared with Evidex. Drafted by an AI model ({model}) from {sources}. Every number was "
            "checked by code against its cited source, and every statement was reviewed and approved by "
            f"{signed}. General teaching material: not advice about any individual patient.")
    if audience == PATIENT_PERSONA:
        note = PATIENT_NOTE + " " + note
    return note


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
    note = ai_note(model, signed, any(r.get("kind") for r in refs.values()), d["audience"])
    return {"draft_id": d["id"], "title": d["title"], "audience": d["audience"],
            "artifact": d["artifact"], "request": d["request"], "signed": signed,
            "sections": sections, "references": sorted(refs.values(), key=lambda r: r["n"]),
            "ai_note": note, "approved_texts": [c["text"] for c in d["claims"] if c["status"] == "approved"],
            "other_texts": [c["text"] for c in d["claims"] if c["status"] != "approved"]}


def build_unsaved(title: str, audience: str, sections: list[dict], signed: str,
                  model: str = "a local language model") -> dict:
    """Document model from in-memory claims marked approved (used by the evaluation only;
    real exports always go through build() on a signed-off draft)."""
    refs: dict[str, dict] = {}
    out, approved, other = [], [], []
    for s in sections:
        items = []
        for c in s["claims"]:
            if not c.get("approved"):
                other.append(c["text"])
                continue
            approved.append(c["text"])
            nums = []
            for an in c["verdict"].get("anchors", []):
                refs.setdefault(an["assertion_id"], {"n": len(refs) + 1, **an})
                nums.append(refs[an["assertion_id"]]["n"])
            items.append({"claim_id": None, "text": c["text"], "refs": sorted(set(nums)),
                          "assertion_ids": c["assertion_ids"], "grades": c["verdict"].get("grades", [])})
        out.append({"heading": s["heading"], "items": items})
    return {"draft_id": None, "title": title, "audience": audience, "artifact": "slides",
            "request": "", "signed": signed, "sections": out,
            "references": sorted(refs.values(), key=lambda r: r["n"]),
            "ai_note": f"Evaluation export ({model}); not signed off.",
            "approved_texts": approved, "other_texts": other}


def ref_tag(refs: list[int]) -> str:
    return " [" + ", ".join(map(str, refs)) + "]" if refs else ""


def reference_text(r: dict) -> str:
    if r.get("kind"):  # PubMed record or uploaded file: the stored citation line
        return r["anchor"]
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


# ---------------- flowchart (PPTX, drawn by code from approved claims) ----------------

def to_flowchart(doc: dict) -> bytes:
    """One slide per up to 6 steps: each section is a step box (heading + its approved statements),
    joined by arrows in reading order. References and the note follow on their own slides."""
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.oxml.ns import qn
    from pptx.util import Inches, Pt

    INK, MUTED, ACCENT, FILL = (RGBColor(0x1F, 0x29, 0x37), RGBColor(0x5B, 0x67, 0x75),
                                RGBColor(0x0F, 0x76, 0x6E), RGBColor(0xEC, 0xF6, 0xF5))
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    steps_ = [s for s in doc["sections"] if s["items"]]

    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Inches(0.5), Inches(0.25), Inches(12.3), Inches(0.8)).text_frame
    tb.word_wrap = True
    tb.text = doc["title"]
    tb.paragraphs[0].font.size, tb.paragraphs[0].font.bold, tb.paragraphs[0].font.color.rgb = Pt(26), True, INK
    n = len(steps_)
    cols = min(n, 3) if n > 1 else 1
    rows = -(-n // cols)
    gap_x, gap_y = Inches(0.55), Inches(0.45)
    top, left = Inches(1.2), Inches(0.5)
    bw = int((Inches(12.33) - gap_x * (cols - 1)) / cols)
    bh = int((Inches(5.6) - gap_y * (rows - 1)) / rows)
    longest = max((sum(len(i["text"]) for i in st["items"]) for st in steps_), default=0)
    size = 13 if longest < 220 and rows == 1 else 11 if longest < 330 else 9.5
    boxes = []
    for k, st in enumerate(steps_):
        r, c = divmod(k, cols)
        x, y = left + c * (bw + gap_x), top + r * (bh + gap_y)
        shp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, x, y, bw, bh)
        shp.adjustments[0] = 0.08
        shp.fill.solid()
        shp.fill.fore_color.rgb = FILL
        shp.line.color.rgb = ACCENT
        tf = shp.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Inches(0.12)
        p = tf.paragraphs[0]
        p.text = f"{k + 1}. {st['heading']}"
        p.font.size, p.font.bold, p.font.color.rgb = Pt(size + 2), True, ACCENT
        for i in st["items"]:
            q = tf.add_paragraph()
            q.text = "• " + i["text"] + ref_tag(i["refs"])
            q.font.size, q.font.color.rgb = Pt(size), INK
            q.space_before = Pt(4)
        boxes.append(shp)
    for a_, b_ in zip(boxes, boxes[1:]):
        same_row = abs(a_.top - b_.top) < Inches(0.1)
        if same_row:
            x1, y1, x2, y2 = a_.left + a_.width, a_.top + a_.height // 2, b_.left, b_.top + b_.height // 2
        else:
            x1, y1, x2, y2 = a_.left + a_.width // 2, a_.top + a_.height, b_.left + b_.width // 2, b_.top
        ln = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
        ln.line.color.rgb, ln.line.width = ACCENT, Pt(2)
        end = ln.line._get_or_add_ln()
        tail = end.makeelement(qn("a:tailEnd"), {"type": "triangle"})
        end.append(tail)
    foot = s.shapes.add_textbox(Inches(0.5), Inches(7.0), Inches(12.3), Inches(0.4)).text_frame
    foot.text = f"Evidex · {doc['audience']} · approved by {doc['signed']}"
    foot.paragraphs[0].font.size, foot.paragraphs[0].font.color.rgb = Pt(10), MUTED

    s = prs.slides.add_slide(blank)
    tf = s.shapes.add_textbox(Inches(0.6), Inches(0.4), Inches(12.1), Inches(6.6)).text_frame
    tf.word_wrap = True
    tf.text = "References"
    tf.paragraphs[0].font.size, tf.paragraphs[0].font.bold = Pt(24), True
    for r in doc["references"]:
        p = tf.add_paragraph()
        p.text = f"[{r['n']}] {reference_text(r)}"
        p.font.size = Pt(10 if len(doc["references"]) > 8 else 12)
    p = tf.add_paragraph()
    p.text = doc["ai_note"]
    p.font.size, p.font.color.rgb = Pt(11), MUTED
    p.space_before = Pt(14)
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ---------------- one-page summary (PDF) ----------------

FONT_DIR = __import__("pathlib").Path(__file__).parent / "assets" / "fonts"
MIN_PT = 7.5


def to_onepager(doc: dict) -> bytes:
    """A4, one page. The font shrinks down to MIN_PT; if it still does not fit, export is blocked."""
    from fpdf import FPDF

    def render(pt: float) -> FPDF:
        pdf = FPDF(format="A4", unit="mm")
        pdf.set_auto_page_break(True, margin=12)
        pdf.set_margins(14, 12, 14)
        pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
        pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        pdf.add_page()
        lh = pt * 0.48
        pdf.set_font("DejaVu", "B", pt + 7)
        pdf.set_text_color(31, 41, 55)
        pdf.multi_cell(0, (pt + 7) * 0.5, doc["title"], new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", pt)
        pdf.set_text_color(91, 103, 117)
        pdf.multi_cell(0, lh, f"Quick reference for {doc['audience']} · approved by {doc['signed']}",
                       new_x="LMARGIN", new_y="NEXT")
        pdf.ln(lh * 0.6)
        for sec in doc["sections"]:
            pdf.set_font("DejaVu", "B", pt + 2)
            pdf.set_text_color(15, 118, 110)
            pdf.multi_cell(0, (pt + 2) * 0.5, sec["heading"], new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("DejaVu", "", pt)
            pdf.set_text_color(31, 41, 55)
            for i in sec["items"] or [{"text": NOT_PROVIDED, "refs": []}]:
                pdf.set_x(pdf.l_margin + 3)
                pdf.multi_cell(0, lh, "• " + i["text"] + ref_tag(i["refs"]), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(lh * 0.4)
        pdf.set_font("DejaVu", "B", pt)
        pdf.multi_cell(0, lh, "References", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", pt - 1)
        for r in doc["references"]:
            pdf.multi_cell(0, (pt - 1) * 0.45, f"[{r['n']}] {reference_text(r)}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(lh * 0.4)
        pdf.set_text_color(91, 103, 117)
        pdf.multi_cell(0, (pt - 1) * 0.45, doc["ai_note"], new_x="LMARGIN", new_y="NEXT")
        return pdf

    pt = 10.0
    while pt >= MIN_PT:
        pdf = render(pt)
        if pdf.page_no() == 1:
            return bytes(pdf.output())
        pt -= 0.5
    raise ExportError("the content does not fit on one page; remove a few statements or choose the handout")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages)


# ---------------- entry points ----------------

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
FORMATS = {  # fmt: (make, read back, mime, file extension)
    "pptx": (to_pptx, pptx_text, PPTX_MIME, "pptx"),
    "docx": (to_docx, docx_text, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
    "flowchart": (to_flowchart, pptx_text, PPTX_MIME, "pptx"),
    "pdf": (to_onepager, pdf_text, "application/pdf", "pdf"),
}
# What each artifact offers for download, main file first.
DOWNLOADS = {"slides": ["pptx", "docx"], "handout": ["docx", "pptx"], "flowchart": ["flowchart", "pdf"],
             "onepager": ["pdf", "docx"]}
LABELS = {"pptx": "Slides (PPTX)", "docx": "Handout (DOCX)", "flowchart": "Flowchart (PPTX)",
          "pdf": "One-page summary (PDF)"}


def render(conn, draft_id: str, fmt: str) -> dict:
    """Build, check and render. Raises ExportError if any fidelity check fails."""
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt}")
    doc = build(conn, draft_id)
    problems = fidelity(doc)
    make, read, mime, ext = FORMATS[fmt]
    try:
        data = make(doc) if not problems else b""
    except ExportError as e:
        problems, data = [str(e)], b""
    if not problems:
        problems = check_rendered(doc, read(data))
    if problems:
        raise ExportError("export blocked: " + "; ".join(problems))
    slug = re.sub(r"[^a-z0-9]+", "-", doc["title"].lower()).strip("-")[:50] or "evidex"
    return {"filename": f"{slug}{'-flowchart' if fmt == 'flowchart' and 'flowchart' not in slug else ''}.{ext}", "data": data, "mime": mime,
            "sha256": hashlib.sha256(data).hexdigest(),
            "claims": [i["claim_id"] for s in doc["sections"] for i in s["items"]]}


def log_export(conn, draft_id: str, fmt: str, actor: str, sha256: str, claims: list[int]):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("insert into change_log (record_table, record_id, action, actor, after) "
                    "values ('export', %s, %s, %s, %s)",
                    (draft_id, f"export:{fmt}", actor.strip() or "unknown",
                     Jsonb({"sha256": sha256, "claim_ids": claims, "at": datetime.now().isoformat()})))
