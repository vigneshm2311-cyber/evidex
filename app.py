"""Evidex: request → building → draft. Teaching material grounded in the diabetes spine and recent research,
approved by a doctor before download. The Library keeps the spine encyclopedia, graph and review.

Run:  .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation

import psycopg
import streamlit as st
from dotenv import load_dotenv

from evidex import drafts, export, jobs, studio
from evidex.agents import steps
from evidex.spine import api
from evidex.spine.vocab import OPERATORS, STATUSES

load_dotenv()
st.set_page_config(page_title="Evidex", page_icon="📚", layout="wide")
try:  # Streamlit Cloud keeps settings in st.secrets; copy plain values into the environment
    for _k, _v in (st.secrets.items() if st.secrets.load_if_toml_exists() else []):
        if isinstance(_v, (str, int, float)) and _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:  # no secrets file when running locally
    pass

HOSTED = os.environ.get("LLM_PROVIDER", "ollama").lower() == "groq"
MODEL_SHORT = ("a hosted model; usually 1–3 minutes" if HOSTED
               else "a local model on this Mac; usually 1–6 minutes")
MODEL_ABOUT = ("Runs on a **hosted open model (Qwen3.8-27B on Groq)**: your request and the spine rows are sent to it, "
               "so enter general topics only, never patient details." if HOSTED else
               "Runs on a **local model on the team's Mac** (no patient data, no external AI service).")


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

PING_EVERY_S = 30


def fresh_connection():
    # Keepalives and a TCP user timeout make a connection that died with the network
    # fail within seconds instead of hanging every page until the OS gives up.
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10,
                           keepalives=1, keepalives_idle=20, keepalives_interval=5,
                           keepalives_count=3, tcp_user_timeout=15000)


@st.cache_resource
def _connection():
    return fresh_connection()


def conn():
    import time
    c = _connection()
    stale = time.monotonic() - getattr(c, "_evidex_ping", 0) > PING_EVERY_S
    if not (c.closed or c.broken) and stale:
        try:
            c.execute("select 1")
            c._evidex_ping = time.monotonic()
        except psycopg.Error:
            pass
    if c.closed or c.broken:
        try:
            c.close()
        except Exception:
            pass
        _connection.clear()
        c = _connection()
        c._evidex_ping = time.monotonic()
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

def _session_token(code: str) -> str:
    """Kept in the URL so a page refresh does not log an evaluator out. Keyed with a server
    secret, so the access code cannot be worked out from it."""
    import hashlib
    import hmac
    secret = os.environ.get("SESSION_SECRET") or os.environ.get("DOCTOR_CODE") or code
    return hmac.new(secret.encode(), b"evaluator:" + code.encode(), hashlib.sha256).hexdigest()[:24]


def gate():
    """ACCESS_CODE: evaluators (everything except editing the spine).
    DOCTOR_CODE: the doctor (can also edit the spine). Without codes set: local doctor mode."""
    code, doctor = os.environ.get("ACCESS_CODE"), os.environ.get("DOCTOR_CODE")
    if st.session_state.get("authorised"):
        return
    if not code and not doctor:
        st.session_state.update(authorised=True, role="doctor")
        return
    if code and st.query_params.get("s") == _session_token(code):
        st.session_state.update(authorised=True, role="evaluator")
        return
    st.title("Evidex")
    st.caption("Doctor productivity and knowledge assistant · Health-a-thon 2026 · Team Synexis (AZYZ69)")
    st.markdown("Make **slides, handouts, flowcharts and one-page summaries** on diabetes diagnosis, classification, "
                "screening and prevention, from a sentence or your own papers. Grounded in a doctor-curated knowledge "
                "base built from the *ADA Standards of Care 2026* and recent PubMed research; every number is checked "
                "against its source, and nothing is downloaded until you approve it.")
    with st.form("login"):
        entered = st.text_input("Access code", type="password", help="Given in the submission form.")
        submitted = st.form_submit_button("Enter", type="primary")
    if submitted and entered:
        entered = entered.strip()
        if doctor and entered == doctor:
            st.session_state.update(authorised=True, role="doctor")
            st.rerun()
        if code and entered == code:
            st.session_state.update(authorised=True, role="evaluator")
            st.query_params["s"] = _session_token(code)
            st.rerun()
        st.error("That code is not right.")
    st.stop()


def is_doctor() -> bool:
    return st.session_state.get("role") == "doctor"


# ---------- shared limits for a public link ----------

RUNS_PER_HOUR = int(os.environ.get("RUNS_PER_HOUR", "10"))


@st.cache_resource
def model_lock():
    """One model job at a time across all visitors (the local model can only do one)."""
    import threading
    return threading.Lock()


def run_allowed() -> str | None:
    import time as _t
    now = _t.time()
    recent = [t for t in st.session_state.get("run_times", []) if now - t < 3600]
    st.session_state.run_times = recent
    if len(recent) >= RUNS_PER_HOUR:
        return f"Limit reached: {RUNS_PER_HOUR} drafts per hour per visitor. Please try again later."
    return None


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
        if editable and is_doctor():
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
    if cid != default:
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
    if not is_doctor():
        st.info("**How the spine is being verified.** Every row was entered from the ADA Standards of Care "
                "(Sections 2–3). The doctor is now checking each one against the 2026 text and adding its "
                "recommendation number and page. Until a row is checked, drafts and exported files say "
                "*not yet checked by the doctor*. Nothing here is filled in by AI.")
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


# ---------- review (doctor) ----------

STATUS_BADGE = {"pending": ":orange[● to review]", "approved": ":green[✔ approved]",
                "rejected": ":gray[✖ rejected]"}


def _who() -> str:
    return (st.session_state.get("reviewer") or st.session_state.get("approver") or "").strip()


def _act(fn, *args, ok_msg: str = "", **kw):
    try:
        out = fn(*args, **kw)
    except drafts.ReviewError as e:
        st.session_state.flash_error = str(e)
        return None
    except psycopg.Error as e:
        st.session_state.flash_error = f"Not saved: {getattr(e.diag, 'message_primary', None) or e}"
        return None
    if ok_msg:
        st.session_state.flash = ok_msg
    return out


def claim_card(c: dict, locked: bool, ctx: str):
    v = c["verdict"]
    key = f"{ctx}-{c['id']}"
    with st.container(border=True):
        top = STATUS_BADGE[c["status"]] + "  ·  " + (":green[Verifier ✔]" if v.get("ok") else ":red[Verifier ⚠]")
        if v.get("regenerated"):
            top += "  ·  rewritten once by the Verifier"
        if v.get("edited"):
            top += "  ·  edited by doctor"
        st.markdown(top)

        editing = (not locked and c["status"] != "rejected"
                   and st.session_state.get(f"edit-{key}", False))
        if not editing:
            st.markdown(("~~" + c["text"] + "~~") if c["status"] == "rejected" else c["text"])
        else:
            with st.form(f"editform-{key}", border=False):
                new_text = st.text_area("Claim", c["text"], key=f"txt-{key}", height=80,
                                        label_visibility="collapsed")
                save = st.form_submit_button("Save edit and re-verify", type="primary")
            if save and new_text.strip() != c["text"]:
                st.session_state[f"edit-{key}"] = False
                lock = model_lock()
                if not lock.acquire(timeout=20):
                    st.session_state.flash_error = ("The model is busy with another draft; "
                                                    "try saving again in a minute.")
                else:
                    try:
                        with st.spinner("Re-verifying…"):
                            _act(drafts.edit, conn(), model(), c["id"], _who(), new_text,
                                 ok_msg="Edit saved and re-verified")
                    finally:
                        lock.release()
                st.rerun()
        if c["original_text"] != c["text"]:
            st.caption(f"Original draft: {c['original_text']}")

        nums = v.get("number_checks", [])
        if nums:
            st.caption("Numbers: " + "  ".join(
                f"{'✔' if n['ok'] else '✖'} {n['value']} {n['unit']}".strip() for n in nums))
        for issue in v.get("issues", []):
            st.warning(issue)
        if v.get("support"):
            st.caption(f"Support judge: {v['support']['verdict'].replace('_', ' ')} — {v['support']['reason']}")
        st.caption("Grade (from spine): " + ", ".join(sorted(set(v.get("grades", [])))) + " · cites "
                   + ", ".join(f"`{x}`" for x in c["assertion_ids"]))

        if not locked:
            for n in nums:
                if not n["ok"] and n.get("assertion_id") and not n.get("range"):
                    if st.button(f"Propose {n['value']} {n['unit']} as a spine correction",
                                 key=f"prop-{key}-{n['value']}"):
                        new_id = _act(drafts.propose_correction, conn(), c["id"], _who(), n)
                        if new_id:
                            load.clear()
                            st.session_state.flash = (f"Proposed {new_id}. The spine is unchanged until you accept it "
                                                      "in the Encyclopedia.")
                        st.rerun()

        if st.toggle("Source", key=f"src-{key}"):
            for an in v.get("anchors", []):
                st.markdown(f"> {an['statement']}" + (f" — {an['condition_text']}" if an.get("condition_text") else ""))
                st.caption(f"📍 {an['anchor']} · `{an['assertion_id']}` · "
                           + ("✔ checked against 2026 text" if an["doctor_checked"] else "○ not yet checked by doctor"))

        if not locked:
            b1, b2, b3, b4, b5 = st.columns([1.25, 1.15, 1.05, 0.95, 1.15])
            b5.toggle("Edit", key=f"edit-{key}", disabled=c["status"] == "rejected")
            ticked = b1.checkbox("Numbers checked", c["numbers_checked"], key=f"num-{key}",
                                 disabled=not nums) if nums else False
            if b2.button("Approve", key=f"ok-{key}", disabled=c["status"] == "approved"):
                _act(drafts.decide, conn(), c["id"], _who(), "approved", numbers_checked=ticked)
                st.rerun()
            if b3.button("Reject", key=f"no-{key}", disabled=c["status"] == "rejected"):
                _act(drafts.decide, conn(), c["id"], _who(), "rejected")
                st.rerun()
            if b4.button("Undo", key=f"undo-{key}", disabled=c["status"] == "pending"):
                _act(drafts.decide, conn(), c["id"], _who(), "pending")
                st.rerun()


def review_draft(draft_id: str, ctx: str):
    d = drafts.load(conn(), draft_id)
    if d is None:
        st.info("Draft not found.")
        return
    locked = d["status"] == "signed_off"
    st.subheader(d["title"])
    st.caption(f"{d['artifact']} · {d['audience']} · request: “{d['request']}” · draft `{d['id']}`")
    state = drafts.signoff_state(d)
    flagged = sum(not c["verdict"].get("ok") and c["status"] != "rejected" for c in d["claims"])
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Approved", state["approved"])
    m2.metric("Rejected", state["rejected"])
    m3.metric("To review", state["pending"])
    m4.metric("Flagged (open)", flagged)
    if locked:
        st.success(f"Signed off by **{d['signed_by']}** on {d['signed_at'].astimezone():%d %b %Y %H:%M}. Locked.")
    elif not _who():
        st.info("Enter your name in the sidebar to review; every decision is logged with it.")
    if not locked:
        ready = [c for c in d["claims"] if c["status"] == "pending" and c["verdict"].get("ok")]
        if len(ready) > 1:
            with st.container(border=True):
                a1, a2 = st.columns([3, 1.3])
                sure = a1.checkbox(f"I have read the {len(ready)} Verifier-passed claims still to review "
                                   "and checked their numbers against the sources", key=f"allnum-{ctx}-{d['id']}")
                if a2.button(f"Approve all {len(ready)}", disabled=not sure, key=f"allok-{ctx}-{d['id']}"):
                    done = 0
                    for c in ready:
                        if _act(drafts.decide, conn(), c["id"], _who(), "approved", numbers_checked=True) is None:
                            break
                        done += 1
                    if done:
                        st.session_state.flash = f"Approved {done} claim(s)"
                    st.rerun()
                a1.caption("Or approve one by one below. Claims flagged by the Verifier must be fixed or rejected.")

    unused = d.get("unused_rows") or {}
    by_id = {a["id"]: a for a in load()[1]}
    for s in d["sections"]:
        st.markdown(f"#### {s['heading']}")
        for c in s["claims"]:
            claim_card(c, locked, ctx)
        left_out = [by_id[i] for i in unused.get(s["heading"], []) if i in by_id]
        if left_out and not locked:
            st.warning("**Planned for this section but not in any claim.** Check whether the slides need it "
                       "(edit a claim to add it, or ignore):\n\n"
                       + "\n".join(f"- {api.statement(a)} (`{a['id']}`)" for a in left_out))

    st.divider()
    st.markdown("### Sign-off")
    if state["unchecked_rows"]:
        rows_txt = ", ".join(f"`{x}`" for x in state["unchecked_rows"])
        if is_doctor():
            st.warning(f"{len(state['unchecked_rows'])} spine row(s) behind approved claims are not yet checked "
                       f"against your 2026 copy: {rows_txt}")
        else:
            st.info(f"{len(state['unchecked_rows'])} spine row(s) behind these claims are still pending the "
                    f"doctor's check against the ADA 2026 text ({rows_txt}). The exported file says so.")
    if locked:
        st.markdown("### Export")
        st.caption("The file contains exactly the approved claim text, numbered references to the "
                   "spine and its ADA anchors, and a note on how it was made. Every export is logged.")
        order = ["pptx", "docx"] if d["artifact"] == "slides" else ["docx", "pptx"]
        cols = st.columns(2)
        for col, fmt in zip(cols, order):
            try:
                out = export.render(conn(), d["id"], fmt)
            except export.ExportError as e:
                col.error(str(e))
                continue
            col.download_button(
                {"pptx": "⬇ Slides (PPTX)", "docx": "⬇ Handout (DOCX)"}[fmt], out["data"],
                file_name=out["filename"], mime=out["mime"], key=f"dl-{ctx}-{fmt}",
                type="primary" if fmt == order[0] else "secondary",
                on_click=export.log_export,
                args=(conn(), d["id"], fmt, _who() or d["signed_by"], out["sha256"], out["claims"]))
            col.caption(f"Fidelity check passed · SHA-256 `{out['sha256'][:12]}…`")
        return
    for r in state["reasons"]:
        st.caption(f"• {r}")
    if st.button("Sign off this draft", type="primary", disabled=not state["can_sign"], key=f"sign-{ctx}"):
        _act(drafts.sign_off, conn(), d["id"], _who(), ok_msg="Signed off. Export is unlocked.")
        st.rerun()


@st.cache_resource
def model():
    from evidex.llm import make_llm
    return make_llm()


# ================= the three-phase flow =================

FORMAT_LABELS = {"slides": "🖥️ Slides", "handout": "📄 Handout", "flowchart": "🔀 Flowchart",
                 "onepager": "📝 One-page summary"}
FORMAT_NOUN = {"slides": "slide deck", "handout": "handout", "flowchart": "flowchart",
               "onepager": "one-page summary"}
EXAMPLES = [
    "Slides on diagnosing diabetes and prediabetes for final-year students",
    "Flowchart for screening type 2 diabetes in adults",
    "When is HbA1c unreliable? Handout for residents",
    "One-page summary on preventing type 2 diabetes for nurses",
    "Explaining prediabetes to patients",
]
PLAIN_STEPS = [("files", "Reading your files"), ("spine_lookup", "Checking the guideline"),
               ("research", "Finding recent research"), ("planner", "Planning the outline"),
               ("writing", "Writing and checking every number")]


def sx() -> dict:
    """This visitor's state in the flow."""
    if "sx" not in st.session_state:
        st.session_state.sx = {"phase": "request", "understanding": None, "files": [], "notice": None}
    return st.session_state.sx


def reset_flow():
    st.session_state.sx = {"phase": "request", "understanding": None, "files": [], "notice": None}
    st.session_state.pop("job_id", None)
    for k in ("job", "draft"):
        st.query_params.pop(k, None)


def stepper(active: int):
    names = ["Your request", "Building", "Your draft"]
    cols = st.columns(3)
    for i, (col, name) in enumerate(zip(cols, names), 1):
        mark = "✅" if i < active else ("🟢" if i == active else "⚪")
        weight = f"**{name}**" if i == active else name
        col.markdown(f"{mark} &nbsp; {i} · {weight}")
    st.divider()


def _pick_suggestion(text: str):
    st.session_state.req_text = text
    sx().update(phase="request", notice=("info", "Suggestion added below: check who it is for and the format, "
                                                 "then press Continue."), suggestions=[])
    st.query_params.pop("job", None)


def suggest_buttons(items: list[str], ctx: str):
    for i, t in enumerate(items):
        st.button(f"→ {t}", key=f"sug-{ctx}-{i}", on_click=_pick_suggestion, args=(t,))


def _use_example():
    if st.session_state.get("example_pick"):
        st.session_state.req_text = st.session_state.example_pick


def phase_request():
    stepper(1)
    if "prefill" in sx():
        st.session_state.req_text = sx().pop("prefill")
    st.markdown("### What would you like to make?")
    st.caption("Describe it in a sentence, attach papers or a deck if you have them, and pick who it is for.")
    if note := sx().get("notice"):
        kind, msg = note
        (st.error if kind == "error" else st.warning if kind == "warning" else st.info)(msg)
        suggest_buttons(sx().get("suggestions") or [], "req")
    st.selectbox("Need an idea?", [""] + EXAMPLES, key="example_pick", on_change=_use_example,
                 format_func=lambda x: x or "Pick an example…")
    with st.form("request_form", border=True):
        st.text_area("Your request", key="req_text", height=90,
                     placeholder="e.g. Slides on diagnosing diabetes in Indian adults for medical students")
        files = st.file_uploader("Attach papers (PDF) or a deck (PPTX) — optional", type=["pdf", "pptx"],
                                 accept_multiple_files=True,
                                 help="Research papers or teaching material only, never patient records.")
        c1, c2 = st.columns(2)
        persona = c1.radio("Who is it for?", steps.AUDIENCES, key="req_persona")
        artifact = c2.radio("Format", list(FORMAT_LABELS), format_func=FORMAT_LABELS.get, key="req_format")
        go = st.form_submit_button("Continue →", type="primary")
    st.caption("Evidex covers the diagnosis, classification, screening and prevention of diabetes, grounded "
               "in the ADA Standards of Care 2026 and recent research. General teaching material only.")
    if not go:
        return
    request = (st.session_state.get("req_text") or "").strip()
    raw = [(f.name, f.getvalue()) for f in (files or [])]
    if not request and not raw:
        sx()["notice"] = ("warning", "Type what you need or attach a paper or deck.")
        st.rerun()
    texts, problems = studio.read_files(raw)
    if problems:
        sx()["notice"] = ("error", " ".join(problems))
        st.rerun()
    limit = run_allowed()
    if limit:
        sx()["notice"] = ("warning", limit)
        st.rerun()
    lock = model_lock()
    if not lock.acquire(timeout=30):
        sx()["notice"] = ("warning", "Evidex is busy with another request. Please try again in a minute.")
        st.rerun()
    try:
        with st.spinner("Reading your request…"):
            res = studio.understand(conn(), model(), request, persona, artifact, texts)
    finally:
        lock.release()
    st.session_state.run_times.append(__import__("time").time())
    status = res["status"]
    if status == "ok":
        sx().update(phase="confirm", understanding=res["understanding"], files=texts, notice=None, suggestions=[])
    elif status == "declined":
        msg = ("Evidex makes general teaching material and does not advise on individual patients. "
               + (f"Try instead: “{res['general_query']}”" if res.get("general_query") else ""))
        sx()["notice"] = ("error", msg)
        if res.get("general_query"):
            sx()["prefill"] = res["general_query"]
    elif status == "not_found":
        sx()["notice"] = ("warning", f"**Evidex can't make this one yet.** {res['reason']} It covers the diagnosis, "
                                     "classification, screening and prevention of diabetes. Close to what you "
                                     "asked, it can make:")
        sx()["suggestions"] = res.get("suggestions", [])
    else:
        sx()["notice"] = ("error", res.get("message", "Something went wrong. Please try again."))
    st.rerun()


def phase_confirm():
    stepper(1)
    u = sx()["understanding"]
    st.markdown("### Here's what I understood")
    st.caption("Change anything that is not right, then build. Nothing is written until you confirm.")
    with st.form("confirm_form", border=True):
        title = st.text_input("Title", u["title"])
        topic = st.text_input("Topic", u["topic"])
        points = st.text_area("It will answer", "\n".join(u["key_points"]), height=130,
                              help="One question per line.")
        c1, c2 = st.columns(2)
        persona = c1.selectbox("For", steps.AUDIENCES, steps.AUDIENCES.index(u["persona"]))
        artifact = c2.selectbox("Format", list(FORMAT_LABELS), list(FORMAT_LABELS).index(u["artifact"]),
                                format_func=FORMAT_LABELS.get)
        if u.get("files"):
            st.caption("📎 Using your files: " + ", ".join(u["files"])
                       + (f" — {u['from_files']}" if u.get("from_files") else ""))
        st.caption("Sources: the ADA Standards of Care 2026 knowledge base, recent PubMed research"
                   + (" and your files." if u.get("files") else "."))
        b1, b2 = st.columns([1, 4])
        build = b1.form_submit_button("✓ Build it", type="primary")
        back = b2.form_submit_button("← Change my request")
    if back:
        sx().update(phase="request", understanding=None)
        st.rerun()
    if not build:
        return
    key_points = [p.strip("-• ").strip() for p in points.splitlines() if p.strip()]
    if not topic.strip() or not key_points:
        st.error("Give a topic and at least one question.")
        return
    u = {**u, "title": title.strip() or u["title"], "topic": topic.strip(), "key_points": key_points[:6],
         "persona": persona, "artifact": artifact}
    lock = model_lock()
    if not lock.acquire(blocking=False):
        st.warning("Evidex is busy with another draft. Please try again in a minute.")
        return
    llm, files = model(), sx()["files"]

    def run():
        c = fresh_connection()
        try:
            yield from studio.build(c, llm, u, files)
        finally:
            c.close()

    job = jobs.start(run, label=u["title"], on_finish=lock.release)
    st.session_state.job_id = job.id
    st.query_params["job"] = job.id
    sx().update(phase="building", understanding=u)
    st.rerun()


@st.fragment(run_every=3)
def building_progress(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return
    if job.done:
        st.rerun()
    seen = {e["step"].split(":")[0] for e in job.events}
    has_files = bool(sx().get("files")) or "files" in seen
    with st.container(border=True):
        art = (sx().get("understanding") or {}).get("artifact")
        st.markdown(f"#### Building your {FORMAT_NOUN.get(art, 'draft')}…")
        st.caption(f"{job.elapsed:.0f} s so far · usually 2–5 minutes. You can leave this page; the draft is "
                   "saved to **My drafts** when it is ready.")
        order = [k for k, _ in PLAIN_STEPS if k != "files" or has_files]
        current = next((k for k in order if k not in seen), None)
        for key, label in PLAIN_STEPS:
            if key not in order:
                continue
            if key in seen and key != current and key != "writing":
                st.markdown(f"✅ {label}")
            elif key == current or (key == "writing" and current is None):
                st.markdown(f"⏳ **{label}**")
            else:
                st.markdown(f"⚪ {label}")
        written = [e["step"].split(":", 1)[1] for e in job.events if e["step"].startswith("writing:")]
        if written:
            st.caption("Written: " + " · ".join(written))


def phase_building():
    stepper(2)
    job_id = st.session_state.get("job_id") or st.query_params.get("job")
    job = jobs.get(job_id)
    if job is None:
        st.info("This build is no longer running here (the app restarted). Finished drafts are in **My drafts**.")
        if st.button("Start a new request"):
            reset_flow()
            st.rerun()
        return
    st.session_state.job_id = job.id
    if not job.done:
        building_progress(job.id)
        return
    last = job.events[-1] if job.events else {"step": "job", "status": "error", "data": {"error": job.error}}
    d = last["data"]
    if last["step"] == "done" and d.get("draft_id"):
        sx().update(phase="draft", draft_id=d["draft_id"])
        st.query_params["draft"] = d["draft_id"]
        st.query_params.pop("job", None)
        st.rerun()
    if last["status"] == "not_found":
        st.warning(f"**Evidex can't make this one yet.** {d.get('reason', '')} It covers the diagnosis, "
                   "classification, screening and prevention of diabetes. Close to what you asked, it can make:")
        suggest_buttons(d.get("suggestions") or [], "build")
    else:
        why = str(d.get("error") or "no statements were produced").replace("LLMError: ", "")
        st.error(f"The draft could not be finished: {why}. Your request is kept: press **Back** and "
                 "**Build it** again.")
    if st.button("← Back to my request", type="primary"):
        sx().update(phase="confirm")
        st.query_params.pop("job", None)
        st.rerun()


# ---------- phase 3: the draft ----------

def _plain_issue(issue: str) -> str:
    i = issue.lower()
    if "number" in i or "spine says" in i:
        return "A number does not match its source."
    if "support judge" in i:
        return "The source does not fully support this wording."
    if "wording" in i:
        return "The wording looks broken."
    if "patient-directed" in i:
        return "It reads like advice to a patient."
    return issue


def ref_numbers(claims: list[dict]) -> tuple[dict[str, int], list[dict]]:
    refs: dict[str, dict] = {}
    for c in claims:
        if c["status"] == "rejected":
            continue
        for an in c["verdict"].get("anchors", []):
            refs.setdefault(an["assertion_id"], {"n": len(refs) + 1, **an})
    return {k: v["n"] for k, v in refs.items()}, sorted(refs.values(), key=lambda r: r["n"])


def _ref_line(r: dict) -> str:
    if r.get("kind") == "pubmed" and r.get("url"):
        return f"[{r['n']}] {r['anchor'].replace(r['url'], '').strip()} [PubMed ↗]({r['url']})"
    if r.get("kind") == "upload":
        return f"[{r['n']}] {r['anchor']}"
    return f"[{r['n']}] {r['statement']} — {r['anchor']}"


def statement_row(c: dict, nums: dict[str, int], locked: bool, ctx: str):
    v = c["verdict"]
    tag = " " + "".join(f"<sup>[{nums[a['assertion_id']]}]</sup>" for a in v.get("anchors", [])
                        if a["assertion_id"] in nums)
    needs_fix = not v.get("ok") and c["status"] != "rejected"
    left, right = st.columns([14, 1]) if not locked else (st.container(), None)
    if needs_fix:
        left.markdown(f"⚠️ {c['text']}{tag}", unsafe_allow_html=True)
        left.caption("Needs a fix: " + " ".join(dict.fromkeys(_plain_issue(x) for x in v.get("issues", []))))
    else:
        left.markdown(f"• {c['text']}{tag}", unsafe_allow_html=True)
    if locked:
        return
    with right.popover("✏️", help="Edit or remove this statement"):
        with st.form(f"fix-{ctx}-{c['id']}", border=False):
            new = st.text_area("Statement", c["text"], height=100, label_visibility="collapsed")
            s1, s2 = st.columns(2)
            save = s1.form_submit_button("Save and re-check", type="primary")
            remove = s2.form_submit_button("Remove")
        if save and new.strip() and new.strip() != c["text"]:
            lock = model_lock()
            if not lock.acquire(timeout=20):
                st.session_state.flash_error = "Evidex is busy; try saving again in a minute."
            else:
                try:
                    with st.spinner("Checking…"):
                        _act(drafts.edit, conn(), model(), c["id"], _who() or "reviewer", new,
                             ok_msg="Saved and re-checked")
                finally:
                    lock.release()
            st.rerun()
        if remove:
            _act(drafts.decide, conn(), c["id"], _who() or "reviewer", "rejected", ok_msg="Removed")
            st.rerun()


def flowchart_preview(sections: list[dict]):
    lines = ['digraph f {', 'rankdir=TB; bgcolor="transparent"; nodesep=0.3; ranksep=0.35;',
             'node [shape=box style="rounded,filled" fillcolor="#ECF6F5" color="#0F766E" fontname="Helvetica" '
             'fontsize=10 margin="0.15,0.08"];', 'edge [color="#0F766E" penwidth=1.6];']
    import textwrap
    for i, s in enumerate(sections):
        body = [textwrap.fill(c["text"], 48) for c in s["claims"] if c["status"] != "rejected"]
        label = f"{i + 1}. {s['heading']}\\n\\n" + "\\n".join("• " + b.replace("\n", "\\n  ") for b in body)
        lines.append(f'n{i} [label={_q(label)}];')
    for i in range(len(sections) - 1):
        lines.append(f"n{i} -> n{i + 1};")
    lines.append("}")
    st.graphviz_chart("\n".join(lines), use_container_width=False)
    st.caption("Edit or remove any statement in the steps below; the flowchart follows.")


def draft_view(draft_id: str, ctx: str):
    d = drafts.load(conn(), draft_id)
    if d is None:
        st.info("Draft not found.")
        return
    u = d.get("understanding") or {}
    locked = d["status"] == "signed_off"
    art = d["artifact"]
    nums, refs = ref_numbers(d["claims"])
    st.markdown(f"## {d['title']}")
    st.caption(f"{FORMAT_LABELS.get(art, art)} · for {d['audience']}"
               + (" · ✅ approved by " + d["signed_by"] if locked else ""))
    if u.get("research_note"):
        st.info(u["research_note"])

    if art == "flowchart":
        flowchart_preview(d["sections"])
    for i, s in enumerate(d["sections"], 1):
        live = [c for c in s["claims"] if c["status"] != "rejected"]
        with st.container(border=True):
            prefix = {"slides": f"Slide {i} · ", "flowchart": f"Step {i} · "}.get(art, "")
            st.markdown(f"**{prefix}{s['heading']}**")
            if not live:
                st.caption("Not provided (all statements removed).")
            for c in live:
                statement_row(c, nums, locked, ctx)
    if d["audience"] == steps.PATIENT_PERSONA:
        st.caption(steps.PATIENT_NOTE)
    if refs:
        with st.expander(f"References ({len(refs)})", expanded=locked):
            for r in refs:
                st.markdown(_ref_line(r))

    st.divider()
    if locked:
        st.markdown("### ⬇ Download")
        cols = st.columns(len(export.DOWNLOADS.get(art, ["pptx", "docx"])))
        for n, (col, fmt) in enumerate(zip(cols, export.DOWNLOADS.get(art, ["pptx", "docx"]))):
            try:
                out = export.render(conn(), d["id"], fmt)
            except export.ExportError as e:
                col.error(str(e).replace("export blocked: ", ""))
                continue
            col.download_button(export.LABELS[fmt], out["data"], file_name=out["filename"], mime=out["mime"],
                                key=f"dl-{ctx}-{fmt}", type="primary" if n == 0 else "secondary",
                                on_click=export.log_export,
                                args=(conn(), d["id"], fmt, _who() or d["signed_by"], out["sha256"], out["claims"]))
        if ctx == "flow" and st.button("✨ Make something else"):
            reset_flow()
            st.rerun()
    else:
        state = drafts.signoff_state(d)
        open_fixes = [c for c in d["claims"] if c["status"] != "rejected" and not c["verdict"].get("ok")]
        with st.container(border=True):
            st.markdown("### Approve and download")
            name = st.text_input("Your name", value=_who(), key=f"name-{ctx}-{d['id']}",
                                 placeholder="Dr …", help="Recorded with the approval.")
            sure = st.checkbox("I have read this draft and checked its numbers against the references",
                               key=f"sure-{ctx}-{d['id']}")
            if open_fixes:
                st.warning(f"{len(open_fixes)} statement(s) need a fix: edit them or remove them first.")
            go = st.button("✅ Approve and get my file", type="primary",
                           disabled=bool(open_fixes) or not sure or not name.strip(), key=f"approve-{ctx}-{d['id']}")
            if go:
                st.session_state.approver = name.strip()
                ok = True
                for c in d["claims"]:
                    if c["status"] == "pending" and c["verdict"].get("ok"):
                        if _act(drafts.decide, conn(), c["id"], name.strip(), "approved", numbers_checked=True) is None:
                            ok = False
                            break
                if ok and _act(drafts.sign_off, conn(), d["id"], name.strip(),
                               ok_msg="Approved. Your files are ready.") is not None:
                    st.balloons()
                st.rerun()
            if state["unchecked_rows"]:
                st.caption("Some guideline entries are still being checked by our doctor against the 2026 text; "
                           "the file's references say which.")
    with st.expander("Detailed review: statement by statement, with sources and checks"):
        review_draft(d["id"], f"detail-{ctx}")


def phase_draft():
    stepper(3)
    draft_id = sx().get("draft_id") or st.query_params.get("draft")
    if not draft_id:
        reset_flow()
        st.rerun()
    draft_view(draft_id, "flow")


def create_page():
    s = sx()
    if s["phase"] == "request":
        if st.query_params.get("job"):
            s["phase"] = "building"
        elif st.query_params.get("draft"):
            s.update(phase="draft", draft_id=st.query_params["draft"])
    {"request": phase_request, "confirm": phase_confirm, "building": phase_building,
     "draft": phase_draft}[s["phase"]]()


def drafts_page():
    rows = drafts.list_drafts(conn())
    if not rows:
        st.info("No drafts yet. Make one in **Create**.")
        return
    labels = {r["id"]: f"{r['title']} · {r['created_at'].astimezone():%d %b %H:%M} · "
                       + ("✅ approved" if r["status"] == "signed_off" else "in review")
              for r in rows}
    ids = list(labels)
    pick = st.selectbox("Draft", ids, format_func=labels.get)
    draft_view(pick, "mine")


# ---------- how it works ----------

def about_page():
    st.markdown("""
### Evidex: verified diabetes teaching material in minutes

**1 · Your request.** Say what you need in a sentence, attach papers (PDF) or a deck (PPTX) if you
have them, and pick who it is for and the format. Evidex tells you what it understood; you confirm.

**2 · Building.** Evidex grounds the content in a doctor-curated knowledge base built from the
*ADA Standards of Care in Diabetes 2026* (diagnosis, classification, screening, prevention), adds
context from recent PubMed research and your files, writes for your audience and checks every number
against its source.

**3 · Your draft.** Read the preview, fix or remove any statement, approve, and download:
slides, a handout, a flowchart or a one-page summary, with numbered references.

#### Try it (about 5 minutes)
1. In **Create**, pick an example such as *"Flowchart for screening type 2 diabetes in adults"*, choose
   who it is for, and press **Continue**.
2. Check what Evidex understood, edit a question if you like, and press **Build it** (2–5 minutes).
3. Read the draft. Open **✏️ Edit** on a statement and change a number (e.g. 6.5 to 7.0):
   it is flagged and approval is blocked until you fix or remove it.
4. Tick the confirmation, **Approve**, and download the file.
5. Try a request about a specific patient (*"What dose should I start for my 54-year-old patient?"*):
   Evidex declines and suggests a general question.

#### Good to know
- @@MODEL@@
- General teaching material only: Evidex never advises on an individual patient. Attach papers or
  teaching material, never patient records (a check blocks files that look like one).
- Every PubMed reference comes from PubMed itself; every number is checked by code against its source.
- **Library** holds the knowledge base (encyclopedia, graph, and the doctor's verification progress).
""".replace("@@MODEL@@", MODEL_ABOUT))
    st.caption("Team Synexis · AZYZ69 · source code: github.com/vigneshm2311-cyber/evidex")


# ---------- main ----------

gate()
concepts, assertions = load()

with st.sidebar:
    st.markdown("### Evidex")
    st.caption("Diabetes teaching material, grounded in the ADA Standards of Care 2026 and recent research")
    st.text_input("Your name", key="reviewer", placeholder="Dr …", help="Recorded with your approvals and edits.")
    st.caption("Signed in as **doctor** (can edit the knowledge base)" if is_doctor()
               else "Signed in as **evaluator**")
    if st.button("✨ New request", use_container_width=True):
        reset_flow()
        st.rerun()
    if is_doctor() and st.button("Reload knowledge base"):
        load.clear()
        export_files.clear()
        st.rerun()

if msg := st.session_state.pop("flash", None):
    st.toast(msg, icon="✅")
if err := st.session_state.pop("flash_error", None):
    st.toast(err, icon="⛔")
    st.error(err)

tab_create, tab_mine, tab_lib, tab_about = st.tabs(["✨ Create", "📂 My drafts", "📚 Library", "ℹ️ How it works"])
with tab_create:
    create_page()
with tab_mine:
    drafts_page()
with tab_lib:
    l1, l2, l3 = st.tabs(["Encyclopedia", "Graph", "Spine review" if is_doctor() else "Verification"])
    with l1:
        encyclopedia(concepts, assertions)
    with l2:
        graph(concepts, assertions)
    with l3:
        review(concepts, assertions)
with tab_about:
    about_page()
