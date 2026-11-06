"""Evidex: spine encyclopedia (checkpoint 2).

Run:  .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import psycopg
import streamlit as st
from dotenv import load_dotenv

from evidex.spine import api
from evidex.spine.vocab import OPERATORS, STATUSES

load_dotenv()
st.set_page_config(page_title="Evidex · Diabetes spine", page_icon="📚", layout="wide")

BLOCKS = {
    ("2", "a"): "A · Diagnostic criteria",
    ("2", "b"): "B · Prediabetes",
    ("2", "c"): "C · When A1C is unreliable",
    ("2", "d"): "D · Screening adults without symptoms",
    ("2", "e"): "E · Classification and specific types",
    ("2", "f"): "F · Type 1 diabetes staging",
    ("2", "g"): "G · Children and adolescents",
    ("3", ""): "Prevention or delay of diabetes",
}
TYPE_COLOUR = {
    "Condition": "#cfe2ff", "Test": "#d1f0d6", "Population": "#fde2c4", "RiskFactor": "#f6d0d6",
    "Intervention": "#e3d7f7", "DrugClass": "#e3d7f7", "Outcome": "#d6eef2", "Rule": "#eeeeee",
    "Threshold": "#fff3b0", "Complication": "#f6d0d6",
}


# ---------- data ----------

@st.cache_resource
def _connection():
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)


def conn():
    c = _connection()
    if c.closed or c.broken:
        _connection.clear()
        c = _connection()
    return c


@st.cache_data(ttl=300, show_spinner="Loading the spine…")
def load():
    return api.all_concepts(conn()), api.all_assertions(conn())


@st.cache_data(ttl=300, show_spinner=False)
def export_files():
    return api.export_csv(conn())


def block_of(a: dict) -> tuple[str, str]:
    parts = a["id"].split("-")  # ada26-s2-a-...
    sec = a["section"]
    if sec == "2" and len(parts) > 2 and len(parts[2]) == 1:
        return ("2", parts[2])
    return (sec, "")


# ---------- access ----------

def gate():
    code = os.environ.get("ACCESS_CODE")
    if not code or st.session_state.get("authorised"):
        return
    st.title("Evidex")
    st.caption("Private proof of concept · Health-a-thon 2026 · Team Synexis")
    entered = st.text_input("Access code", type="password")
    if entered:
        if entered.strip() == code:
            st.session_state.authorised = True
            st.rerun()
        st.error("That code is not right.")
    st.stop()


# ---------- rendering ----------

def badges(a: dict) -> str:
    b = [f"`{a['status']}`",
         f"grade **{a['grade']}**" if a["grade"] else "grade :orange[TODO(doctor)]",
         ":green[✔ checked against 2026 text]" if a["doctor_checked"] else ":orange[○ not yet checked]"]
    if a["reviewed_by"]:
        b.append(f"reviewed by {a['reviewed_by']}")
    return " · ".join(b)


def _dec(s: str, label: str):
    s = (s or "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        raise ValueError(f"{label} must be a number, not '{s}'")


def edit_form(a: dict, ctx: str):
    with st.form(f"edit-{ctx}-{a['id']}", border=False):
        c1, c2, c3, c4 = st.columns([1, 1.4, 1, 1.2])
        grade = c1.text_input("Grade", a["grade"] or "", key=f"g-{ctx}-{a['id']}")
        locator = c2.text_input("Rec / Table", a["locator"] or "", placeholder="Rec 2.1 or Table 2.2",
                                key=f"l-{ctx}-{a['id']}")
        page = c3.text_input("Page", a["page"] or "", placeholder="S27", key=f"p-{ctx}-{a['id']}")
        checked = c4.checkbox("Checked against my 2026 copy", a["doctor_checked"], key=f"c-{ctx}-{a['id']}")

        st.caption("Value, status and wording: change only if the 2026 text differs")
        if True:
            ops = [""] + sorted(OPERATORS)
            d1, d2, d3, d4, d5 = st.columns([1, 1, 1, 1.3, 1.3])
            op = d1.selectbox("Operator", ops, ops.index(a["operator"] or ""), key=f"o-{ctx}-{a['id']}")
            val = d2.text_input("Value (low)", api._fmt_num(a["value"]), key=f"v-{ctx}-{a['id']}")
            high = d3.text_input("High (ranges)", api._fmt_num(a["value_high"]), key=f"h-{ctx}-{a['id']}")
            unit = d4.text_input("Unit", a["unit"] or "", key=f"u-{ctx}-{a['id']}")
            statuses = sorted(STATUSES)
            status = d5.selectbox("Status", statuses, statuses.index(a["status"]), key=f"s-{ctx}-{a['id']}")
            cond = st.text_area("Condition / qualifier (your words, not pasted ADA text)",
                                a["condition_text"] or "", key=f"t-{ctx}-{a['id']}", height=70)
            notes = st.text_input("Notes", a["notes"] or "", key=f"n-{ctx}-{a['id']}")

        if not st.form_submit_button("Save", type="primary"):
            return
        who = st.session_state.get("reviewer", "").strip()
        if not who:
            st.error("Enter your name in the sidebar first; every edit is logged with it.")
            return
        try:
            new = {
                "grade": grade.strip() or None, "locator": locator.strip() or None,
                "page": page.strip() or None, "doctor_checked": checked,
                "operator": op or None, "value": _dec(val, "Value"),
                "value_high": _dec(high, "High"), "unit": unit.strip() or None,
                "status": status, "condition_text": cond.strip() or None,
                "notes": notes.strip() or None,
            }
            old = {k: a[k] for k in new}
            changes = {k: v for k, v in new.items() if v != old[k]}
            if not changes:
                st.info("Nothing changed.")
                return
            api.update_assertion(conn(), a["id"], who, changes)
        except ValueError as e:
            st.error(str(e))
            return
        except psycopg.Error as e:
            msg = (e.diag.message_primary or str(e)) if hasattr(e, "diag") else str(e)
            st.error(f"Not saved; the database rejected it: {msg}")
            return
        load.clear()
        export_files.clear()
        st.session_state.flash = f"Saved {a['id']} ({', '.join(changes)})"
        st.rerun()


def card(a: dict, ctx: str, editable: bool = True):
    with st.container(border=True):
        st.markdown(f"**{api.statement(a)}**")
        if a["condition_text"]:
            st.caption(a["condition_text"])
        st.caption(f"📍 {api.anchor_text(a)}  ·  `{a['id']}`")
        st.markdown(badges(a))
        if a["notes"]:
            (st.warning if "LOWER CONFIDENCE" in a["notes"] else st.caption)(a["notes"])
        if editable:
            with st.expander("Check / edit"):
                edit_form(a, ctx)
                if st.checkbox("Show change history", key=f"hist-{ctx}-{a['id']}"):
                    hist = api.change_history(conn(), a["id"])
                    st.dataframe(
                        [{"when": h["at"].strftime("%d %b %H:%M"), "who": h["actor"],
                          "action": h["action"]} for h in hist],
                        hide_index=True, use_container_width=True)


def order_key(a: dict):
    for i, (_, preds) in enumerate(api.OUTLINE_ORDER):
        if a["predicate"] in preds:
            return (i, preds.index(a["predicate"]), a["id"])
    return (99, 0, a["id"])


# ---------- pages ----------

def encyclopedia(concepts, assertions):
    by_id = {c["id"]: c for c in concepts}
    counts = {}
    for a in assertions:
        for k in ("subject_id", "object_id", "population_id"):
            if a[k]:
                counts[a[k]] = counts.get(a[k], 0) + 1
    left, mid = st.columns([1, 2])
    q = left.text_input("Search", placeholder="HbA1c, gestational, BMI, metformin…")
    if q:
        ql = q.lower()
        matches = [c for c in concepts if any(ql in t.lower() for t in api._terms(c))]
    else:
        matches = concepts
    if not matches:
        st.info("Not found in the spine.")
        return
    matches = sorted(matches, key=lambda c: (-counts.get(c["id"], 0), c["name"]))
    default = st.query_params.get("concept", "cond:diabetes")
    idx = next((i for i, c in enumerate(matches) if c["id"] == default), 0)
    cid = mid.selectbox(
        f"Concept ({len(matches)} found)", [c["id"] for c in matches], index=idx,
        format_func=lambda i: f"{by_id[i]['name']}  ·  {by_id[i]['type']} · {counts.get(i, 0)} statements")
    st.query_params["concept"] = cid
    with st.container():
        c = by_id[cid]
        st.subheader(c["name"])
        st.caption(f"`{c['id']}` · {c['type']}"
                   + (f" · also: {', '.join(c['synonyms'])}" if c["synonyms"] else ""))
        if c["notes"]:
            st.caption(c["notes"])
        groups = [
            ("Statements about this concept", [a for a in assertions if a["subject_id"] == cid]),
            ("Where it appears in other statements", [a for a in assertions if a["object_id"] == cid]),
            ("Statements that apply to this population",
             [a for a in assertions if a["population_id"] == cid]),
        ]
        shown = False
        for title, rows in groups:
            if not rows:
                continue
            shown = True
            st.markdown(f"##### {title} ({len(rows)})")
            for a in sorted(rows, key=order_key):
                card(a, f"enc{title[:4]}")
        if not shown:
            st.info("No statements reference this concept yet.")


def _q(s: str) -> str:
    return '"' + s.replace('"', "'") + '"'


def graph(concepts, assertions):
    by_id = {c["id"]: c for c in concepts}
    opts = [k for k in BLOCKS if any(block_of(a) == k for a in assertions)]
    c1, c2 = st.columns([2, 1])
    picked = c1.multiselect("Blocks", opts, default=[("2", "a"), ("2", "b"), ("2", "c")],
                            format_func=lambda k: f"Section {k[0]} · {BLOCKS[k]}")
    hide_general = c2.toggle("Hide classification edges", value=False)
    rows = [a for a in assertions if block_of(a) in picked
            and not (hide_general and a["predicate"] in ("classified_as", "subtype_of"))]
    if not rows:
        st.info("Pick at least one block.")
        return
    nodes = {a["subject_id"] for a in rows} | {a["object_id"] for a in rows if a["object_id"]}
    lines = ['digraph spine {', 'rankdir=LR; bgcolor="transparent"; nodesep=0.25; ranksep=0.6;',
             'node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 '
             'color="#888888" fontcolor="#111111"];',
             'edge [fontname="Helvetica" fontsize=9 color="#888888" fontcolor="#777777"];']
    for n in sorted(nodes):
        c = by_id[n]
        lines.append(f'{_q(n)} [label={_q(c["name"])} fillcolor="{TYPE_COLOUR.get(c["type"], "#eee")}" '
                     f'tooltip={_q(c["type"] + " · " + n)}];')
    for a in rows:
        label = a["predicate"].replace("_", " ")
        v = api.format_value(a)
        if v:
            label += f"\\n{v}"
        if a["population_id"] and a["population_id"] != "pop:general":
            label += f"\\n[{a['population_name']}]"
        style = "solid" if a["doctor_checked"] else "dashed"
        target = a["object_id"] or a["subject_id"]
        lines.append(f'{_q(a["subject_id"])} -> {_q(target)} [label={_q(label)} style={style} '
                     f'tooltip={_q(a["id"])}];')
    lines.append("}")
    st.graphviz_chart("\n".join(lines), use_container_width=True)
    st.caption(f"{len(rows)} statements, {len(nodes)} concepts · dashed = not yet checked against "
               "the 2026 text · colours: blue condition, green test, orange population, "
               "pink risk factor, purple intervention/drug")


def review(concepts, assertions):
    total = len(assertions)
    checked = sum(a["doctor_checked"] for a in assertions)
    anchored = sum(bool(a["locator"]) for a in assertions)
    graded = sum(bool(a["grade"]) for a in assertions)
    c1, c2, c3 = st.columns(3)
    c1.metric("Checked against 2026 text", f"{checked} / {total}")
    c2.metric("Rec / Table entered", f"{anchored} / {total}")
    c3.metric("Grade entered", f"{graded} / {total}")
    st.progress(checked / total if total else 0.0)

    f1, f2 = st.columns([1, 1])
    sec = f1.selectbox("Section", ["All", "2", "3"])
    only_open = f2.toggle("Only rows not yet checked", value=True)
    rows = [a for a in assertions if (sec == "All" or a["section"] == sec)
            and (not only_open or not a["doctor_checked"])]
    rows.sort(key=lambda a: ("LOWER CONFIDENCE" not in (a["notes"] or ""), a["id"]))
    st.caption(f"{len(rows)} row(s) · lower-confidence rows first")
    PAGE = 15
    pages = max(1, -(-len(rows) // PAGE))
    pg = st.number_input("Page", 1, pages, 1, key="review_page") if pages > 1 else 1
    for a in rows[(pg - 1) * PAGE: pg * PAGE]:
        card(a, "rev")
    st.caption(f"Page {pg} of {pages}")

    st.divider()
    st.markdown("**Export** (the database is now the master copy; these files re-import cleanly)")
    files = export_files()
    d1, d2 = st.columns(2)
    d1.download_button("concepts.csv", files["concepts.csv"], "concepts.csv", "text/csv")
    d2.download_button("assertions.csv", files["assertions.csv"], "assertions.csv", "text/csv")


# ---------- main ----------

gate()
concepts, assertions = load()

with st.sidebar:
    st.markdown("### Evidex")
    st.caption("Diabetes knowledge spine · ADA Standards of Care in Diabetes 2026, Sections 2–3")
    st.text_input("Your name (for the change log)", key="reviewer", placeholder="Dr …")
    st.divider()
    st.caption(f"{len(concepts)} concepts · {len(assertions)} statements")
    st.caption("The spine reports what the guideline says and where. It does not give advice "
               "about any individual patient.")
    if st.button("Reload from database"):
        load.clear()
        export_files.clear()
        st.rerun()

if msg := st.session_state.pop("flash", None):
    st.toast(msg, icon="✅")

tab1, tab2, tab3 = st.tabs(["📚 Encyclopedia", "🕸️ Graph", "✅ Review progress"])
with tab1:
    encyclopedia(concepts, assertions)
with tab2:
    graph(concepts, assertions)
with tab3:
    review(concepts, assertions)
