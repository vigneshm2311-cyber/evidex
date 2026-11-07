"""Sources beyond the spine: uploaded files and PubMed abstracts.

Text is extracted by code. PubMed records come only from the NCBI E-utilities response; the model
never supplies a PMID, title or link. Everything is stored in `source_doc` so a claim can be
re-checked later against exactly the text it was written from.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

MAX_FILES = 3
MAX_MB = 15
MAX_BODY = 200_000          # characters kept per uploaded file
PUBMED_N = 5
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class SourceError(ValueError):
    pass


# ---------------- reading files ----------------

def read_pdf(data: bytes) -> str:
    from pypdf import PdfReader
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [p.extract_text() or "" for p in reader.pages]
    except Exception as e:  # corrupt or encrypted file
        raise SourceError(f"could not read this PDF ({type(e).__name__})") from e
    return _clean("\n\n".join(pages))


def read_pptx(data: bytes) -> str:
    from pptx import Presentation
    try:
        prs = Presentation(io.BytesIO(data))
    except Exception as e:
        raise SourceError(f"could not read this PowerPoint file ({type(e).__name__})") from e
    slides = []
    for n, slide in enumerate(prs.slides, 1):
        texts = [sh.text_frame.text.strip() for sh in slide.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
        if texts:
            slides.append(f"Slide {n}: " + " / ".join(texts))
    return _clean("\n\n".join(slides))


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"-\n(?=[a-z])", "", text)          # words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_upload(name: str, data: bytes) -> str:
    if len(data) > MAX_MB * 1024 * 1024:
        raise SourceError(f"{name} is larger than {MAX_MB} MB")
    low = name.lower()
    if low.endswith(".pdf"):
        text = read_pdf(data)
    elif low.endswith(".pptx"):
        text = read_pptx(data)
    else:
        raise SourceError(f"{name}: only PDF and PPTX files can be used")
    if len(text) < 200:
        raise SourceError(f"{name}: no readable text found (a scanned PDF needs text, not images)")
    return text[:MAX_BODY]


# ---------------- PII check ----------------

PII_PATTERNS = [
    (r"\b(mrn|uhid|ip\s?no|op\s?no|hospital\s?no|patient\s?id|reg(istration)?\s?no)\b\s*[:#.]?\s*\w*\d", "a hospital record number"),
    (r"\b(patient'?s?\s+name|name\s+of\s+(the\s+)?patient)\s*[:\-]", "a patient name field"),
    (r"\b(d\.?o\.?b\.?|date\s+of\s+birth)\s*[:\-]?\s*\d", "a date of birth"),
    (r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)", "an Aadhaar-style number"),
    (r"(?<![\d.])(\+91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\d.])", "a mobile number"),
]


def pii_problems(text: str) -> list[str]:
    t = (text or "").lower()
    return sorted({why for pat, why in PII_PATTERNS if re.search(pat, t)})


# ---------------- storage ----------------

def _upsert(conn, doc: dict) -> dict:
    conn.execute("insert into source_doc (id, kind, title, citation, url, body) values (%s,%s,%s,%s,%s,%s) "
                 "on conflict (id) do update set title = excluded.title, citation = excluded.citation, "
                 "url = excluded.url, body = excluded.body, fetched_at = now()",
                 (doc["id"], doc["kind"], doc["title"], doc["citation"], doc.get("url"), doc["body"]))
    return doc


def save_upload(conn, name: str, text: str) -> dict:
    sid = "up:" + hashlib.sha256(text.encode()).hexdigest()[:12]
    title = re.sub(r"\.(pdf|pptx)$", "", name, flags=re.I)
    return _upsert(conn, {"id": sid, "kind": "upload", "title": title,
                          "citation": f"Uploaded file: {name}", "url": None, "body": text})


def load(conn, ids: list[str]) -> dict[str, dict]:
    ids = [i for i in ids if is_source_id(i)]
    if not ids:
        return {}
    rows = conn.execute("select id, kind, title, citation, url, body from source_doc where id = any(%s)",
                        (ids,)).fetchall()
    return {r[0]: dict(zip(("id", "kind", "title", "citation", "url", "body"), r)) for r in rows}


def is_source_id(i: str) -> bool:
    return i.startswith("pmid:") or i.startswith("up:")


# ---------------- passages ----------------

_STOP = set("a an the and or of in on for to with about at by from into is are was were be been as than "
            "that this these those it its which who what when how not no can may also we our their there".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOP}


def chunks(text: str, size: int = 700) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = ""
        cur = (cur + "\n" + p).strip()
        while len(cur) > size * 1.6:
            cut = cur.rfind(". ", 0, size) + 1 or size
            out.append(cur[:cut].strip())
            cur = cur[cut:].strip()
    if cur:
        out.append(cur)
    return out


def passage(doc: dict, query: str, limit: int = 1800) -> str:
    """The part of a source a model should see for this query: the whole abstract for PubMed,
    the best-matching passages for an uploaded file."""
    body = doc["body"]
    if doc["kind"] == "pubmed" or len(body) <= limit:
        return body[:limit * 2]
    q = _words(query)
    parts = chunks(body)
    ranked = sorted(range(len(parts)), key=lambda i: -len(q & _words(parts[i])))
    keep, total = [], 0
    for i in ranked:
        if total + len(parts[i]) > limit:
            continue
        keep.append(i)
        total += len(parts[i])
    return "\n…\n".join(parts[i] for i in sorted(keep)) or body[:limit]


def as_row(doc: dict, query: str) -> dict:
    """A source in the shape the agents and the Verifier use for cited material."""
    return {**doc, "passage": passage(doc, query)}


# ---------------- PubMed ----------------

def _get(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Evidex/1.0 (teaching-material assistant)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


STUDY_TYPES = ("(review[pt] OR systematic review[pt] OR meta-analysis[pt] OR clinical trial[pt] OR "
               "guideline[pt] OR observational study[pt])")
FILTERS = ' AND hasabstract AND ("last 10 years"[dp]) AND english[la]'


def pubmed_query(concepts: list[str] | str, in_title: int = 2, study_types: bool = True) -> str:
    """The first `in_title` concepts must be in the title, the rest in title or abstract."""
    if isinstance(concepts, str):
        concepts = [concepts]
    clean = [re.sub(r"[^\w\s\-]", " ", c).strip() for c in concepts]
    parts = [f'"{c}"[ti]' if i < in_title else f'"{c}"[tiab]' for i, c in enumerate(clean) if c]
    return " AND ".join(parts) + (" AND " + STUDY_TYPES if study_types else "") + FILTERS


def _esearch(query: str, n: int) -> list[str]:
    q = urllib.parse.urlencode({"db": "pubmed", "term": query, "retmax": n, "sort": "relevance",
                                "retmode": "json", "tool": "evidex"})
    return json.loads(_get(f"{EUTILS}/esearch.fcgi?{q}"))["esearchresult"]["idlist"]


def search_plan(concepts: list[str]) -> list[str]:
    """Queries from strict to loose; results are collected in this order until there are enough."""
    out = []
    for cs in (concepts, concepts[:3], concepts[:2]):
        for ti, pt in ((2, True), (1, True), (1, False)):
            q = pubmed_query(cs, min(ti, len(cs)), pt)
            if q not in out:
                out.append(q)
    return out


def pubmed_search(conn, concepts: list[str] | str, n: int = PUBMED_N) -> list[dict]:
    """Search PubMed (strict query first, then looser ones) and store the records.
    Raises SourceError if PubMed cannot be reached."""
    if isinstance(concepts, str):
        concepts = [concepts]
    concepts = [c for c in concepts if c.strip()]
    if not concepts:
        return []
    try:
        ids: list[str] = []
        for q in search_plan(concepts):
            ids += [i for i in _esearch(q, n) if i not in ids]
            if len(ids) >= 3:
                break
        ids = ids[:n]
        if not ids:
            return []
        q = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "evidex"})
        xml = _get(f"{EUTILS}/efetch.fcgi?{q}", timeout=25)
    except Exception as e:
        raise SourceError(f"PubMed could not be reached ({type(e).__name__})") from e
    docs = parse_pubmed(xml)
    order = {pmid: i for i, pmid in enumerate(ids)}
    docs.sort(key=lambda d: order.get(d["id"][5:], 99))
    return [_upsert(conn, d) for d in docs]


def _text(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def parse_pubmed(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    out = []
    for art in root.iter("PubmedArticle"):
        pmid = _text(art.find(".//MedlineCitation/PMID"))
        title = _text(art.find(".//ArticleTitle")).rstrip(".")
        parts = []
        for ab in art.findall(".//Abstract/AbstractText"):
            label = ab.get("Label")
            parts.append((f"{label.title()}: " if label else "") + _text(ab))
        abstract = "\n".join(p for p in parts if p)
        if not (pmid and title and abstract):
            continue
        journal = _text(art.find(".//Journal/ISOAbbreviation")) or _text(art.find(".//Journal/Title"))
        year = (_text(art.find(".//JournalIssue/PubDate/Year"))
                or _text(art.find(".//JournalIssue/PubDate/MedlineDate"))[:4]
                or _text(art.find(".//ArticleDate/Year")))
        authors = []
        for a in art.findall(".//AuthorList/Author"):
            last, ini = _text(a.find("LastName")), _text(a.find("Initials"))
            if last:
                authors.append(f"{last} {ini}".strip())
            elif _text(a.find("CollectiveName")):
                authors.append(_text(a.find("CollectiveName")))
        who = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
        citation = ". ".join(x.rstrip(".") for x in [who, title, f"{journal} {year}".strip(), f"PMID {pmid}"] if x) + f". {url}"
        out.append({"id": f"pmid:{pmid}", "kind": "pubmed", "title": title, "citation": citation,
                    "url": url, "body": abstract, "year": year, "journal": journal})
    return out
