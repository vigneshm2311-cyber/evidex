"""Three-phase flow: sources, PII check, literature number checks, new exports, persona rules.
Offline: no network and no model."""
import io

import pytest

from evidex import export, sources, studio
from evidex.agents import steps

PUBMED_XML = b"""<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>12345678</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue><ISOAbbreviation>Diabetes Care</ISOAbbreviation></Journal>
<ArticleTitle>HbA1c and glucose in South Asian adults.</ArticleTitle>
<Abstract><AbstractText Label="RESULTS">Among 1,234 adults, 23.4% had HbA1c of 6.5% or more.</AbstractText></Abstract>
<AuthorList><Author><LastName>Rao</LastName><Initials>K</Initials></Author><Author><LastName>Shah</LastName><Initials>P</Initials></Author></AuthorList>
</Article></MedlineCitation></PubmedArticle>
<PubmedArticle><MedlineCitation><PMID>999</PMID><Article><ArticleTitle>No abstract here</ArticleTitle></Article></MedlineCitation></PubmedArticle>
</PubmedArticleSet>"""


def test_parse_pubmed_keeps_only_records_with_abstracts():
    docs = sources.parse_pubmed(PUBMED_XML)
    assert [d["id"] for d in docs] == ["pmid:12345678"]
    d = docs[0]
    assert d["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert d["citation"].startswith("Rao K, Shah P. HbA1c and glucose in South Asian adults. Diabetes Care 2024. PMID 12345678.")
    assert "Results: Among 1,234 adults" in d["body"]


def test_pubmed_query_puts_main_concepts_in_title_and_filters():
    q = sources.pubmed_query(["HbA1c", "diagnosis", "India"], in_title=2)
    assert q.startswith('"HbA1c"[ti] AND "diagnosis"[ti] AND "India"[tiab]')
    assert "hasabstract" in q and "last 10 years" in q
    assert sources.search_plan(["a", "b", "c"])[0] != sources.search_plan(["a", "b", "c"])[-1]


@pytest.mark.parametrize("text", [
    "Patient name: Ravi Kumar", "DOB: 12/03/1980", "MRN 4455661", "UHID: 22019",
    "call 9876543210", "Aadhaar 1234 5678 9012"])
def test_pii_check_blocks_patient_records(text):
    assert sources.pii_problems(text)


def test_pii_check_lets_papers_through():
    paper = ("Among 1,234 adults (mean age 45.6 years), HbA1c ≥6.5% (48 mmol/mol) was found in 23.4%; "
             "p<0.001; doi 10.1016/j.diab.2020.01.002. Patients with diabetes were older.")
    assert sources.pii_problems(paper) == []


def test_read_files_rejects_other_types_and_too_many():
    texts, problems = studio.read_files([("notes.txt", b"hello")])
    assert not texts and "only PDF and PPTX" in problems[0]
    texts, problems = studio.read_files([("a.pdf", b"")] * 4)
    assert problems == ["Attach at most 3 files."]


def _pptx(text: str) -> bytes:
    from pptx import Presentation
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(4)).text_frame.text = text
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def test_pptx_upload_is_read_and_pii_blocked():
    body = "Screening for type 2 diabetes in adults. " * 10
    texts, problems = studio.read_files([("deck.pptx", _pptx(body))])
    assert problems == [] and "Slide 1: Screening" in texts[0]["text"]
    texts, problems = studio.read_files([("case.pptx", _pptx(body + " Patient name: A. Kumar"))])
    assert not texts and "patient details" in problems[0]


def test_passage_picks_matching_part_of_a_long_upload():
    body = "\n\n".join(["Unrelated text about kidneys and blood pressure. " * 12] * 5
                       + ["HbA1c thresholds for diagnosing diabetes in Indian adults are discussed here. " * 5])
    doc = {"id": "up:x", "kind": "upload", "title": "t", "body": body}
    assert "HbA1c thresholds" in sources.passage(doc, "HbA1c diagnosing diabetes Indian", limit=600)


# ---------------- literature numbers ----------------

SRC = {"id": "pmid:1", "kind": "pubmed", "title": "t", "body": "Among 1,234 adults, 23.4% had HbA1c of 6.5% or more."}


def test_number_from_a_cited_paper_passes_and_a_made_up_one_fails():
    ok = steps.check_text("In one cohort, 23.4% of adults had an HbA1c of 6.5% or more.", [], [SRC])
    assert ok["issues"] == []
    bad = steps.check_text("In one cohort, 31.2% of adults had an HbA1c of 6.5% or more.", [], [SRC])
    assert any("31.2" in i and "cited sources" in i for i in bad["issues"])


def test_source_rows_render_for_the_agents():
    line = steps.assertion_line({**SRC, "passage": "Among 1,234 adults"})
    assert line == "pmid:1 | t | Among 1,234 adults"


# ---------------- exports ----------------

def _doc(audience="Medical students", n_claims=4):
    lit = {"assertion_id": "pmid:1", "anchor": "Rao K. A study. Diabetes Care 2024. PMID 1. https://pubmed.ncbi.nlm.nih.gov/1/",
           "statement": "A study", "kind": "pubmed", "url": "https://pubmed.ncbi.nlm.nih.gov/1/"}
    secs = [{"heading": f"Step {k}", "claims": [
        {"text": f"Statement {k}.{j}: HbA1c ≥ 6.5% is diagnostic.", "approved": True, "assertion_ids": ["pmid:1"],
         "verdict": {"anchors": [lit]}} for j in range(n_claims)]} for k in range(1, 4)]
    doc = export.build_unsaved("Test title", audience, secs, "Dr A, 07 Nov 2026")
    doc["ai_note"] = export.ai_note("m", "Dr A", True, audience)
    return doc


@pytest.mark.parametrize("fmt", ["flowchart", "pdf", "pptx", "docx"])
def test_every_format_contains_exactly_the_approved_text(fmt):
    doc = _doc(n_claims=2)
    make, read, _, _ = export.FORMATS[fmt]
    assert export.fidelity(doc) == []
    assert export.check_rendered(doc, read(make(doc))) == []


def test_onepager_is_one_page_or_blocked():
    from pypdf import PdfReader
    data = export.to_onepager(_doc(n_claims=3))
    assert len(PdfReader(io.BytesIO(data)).pages) == 1
    with pytest.raises(export.ExportError, match="one page"):
        export.to_onepager(_doc(n_claims=40))


def test_literature_reference_is_the_stored_citation():
    assert export.reference_text({"kind": "pubmed", "anchor": "Rao K. A study. PMID 1. https://x/"}) == \
        "Rao K. A study. PMID 1. https://x/"


def test_patient_persona_always_carries_the_note():
    assert export.ai_note("m", "Dr A", False, steps.PATIENT_PERSONA).startswith(steps.PATIENT_NOTE)
    assert steps.PATIENT_NOTE not in export.ai_note("m", "Dr A", False, "Medical students")


def test_every_persona_and_format_has_rules():
    assert set(steps.AUDIENCES) <= set(steps.PERSONA_STYLE)
    assert set(steps.ARTIFACTS) == set(export.DOWNLOADS)
    assert all(f in export.FORMATS for fs in export.DOWNLOADS.values() for f in fs)


# ---------------- suggestions on "Not found" ----------------

def test_every_suggestion_passes_the_code_topic_rules():
    from evidex import pipeline
    for text, _ in studio.SUGGESTIONS:
        assert pipeline.out_of_spine(text) == [], text
        assert steps.red_flags(text) == [], text


def test_suggestions_are_close_to_what_was_asked():
    home = studio.suggestions("Diabetes homecare handout, diabetes home management")
    assert home[0].startswith("Preventing type 2 diabetes at home")
    assert studio.suggestions("insulin pump settings for type 1 diabetes")[0].startswith("Type 1 diabetes")
    assert any("gestational" in s for s in studio.suggestions("diabetes in pregnant women"))
    assert len(studio.suggestions("")) == 3


def test_support_judge_lists_each_source_once():
    from evidex.agents import verifier

    class Spy:
        model = "spy"
        def chat_json(self, system, user, schema):
            self.user = user
            return {"verdicts": [{"claim": i, "verdict": "supported", "reason": "ok"} for i in (1, 2, 3)]}
    spy = Spy()
    src = {**SRC, "passage": SRC["body"]}
    verifier.judge_support(spy, [("a", [src]), ("b", [src]), ("c", [src])], run_id="t")
    assert spy.user.count("Among 1,234 adults") == 1
    assert "Claim 3 (its sources: pmid:1): c" in spy.user


def test_type_2_is_not_a_patient_number():
    assert steps._general_query_problems("Screening for type 2 diabetes in adults",
                                         "Flowchart for screening type 2 diabetes in adults") == []
    assert steps._general_query_problems("What does an HbA1c of 7.2 mean?",
                                         "My patient's HbA1c is 7.2") != []


def test_guideline_words_are_not_topics():
    from evidex import pipeline
    words = [w for w in "type 2 diabetes screening guidelines recommendations latest evidence".split()
             if len(w) > 2 and w not in pipeline.GENERIC_WORDS]
    assert words == []
